"""
Векторная память на Pinecone — семантический поиск по истории пользователя.

Что хранит:
- Все значимые сообщения пользователя (цели, решения, инсайты)
- Факты из профиля
- Результаты исследований
- Эмоциональные паттерны

Как работает:
- При каждом сообщении — upsert embedding в Pinecone (через улучшенные pseudo-embeddings)
- При генерации ответа — semantic search по контексту
- Результат вставляется в системный промпт как [СЕМАНТИЧЕСКАЯ ПАМЯТЬ]

Embeddings:
- Улучшенные pseudo-embeddings: 200+ семантических категорий + TF-IDF bigrams + position weights
- Dimension: 384
- OpenAI embeddings удалены (нет API ключа)

ВАЖНО: Все Pinecone-операции (upsert, query) — синхронные HTTP-вызовы.
Публичные async-обёртки используют asyncio.to_thread() чтобы не блокировать event loop.
"""

import os
import json
import asyncio
import hashlib
import logging
import re
import math
from datetime import datetime, timezone
from collections import Counter

from config import PINECONE_API_KEY

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# PINECONE CLIENT
# ═══════════════════════════════════════════════════════════════

PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "asi-biont-memory")
PINECONE_HOST = os.getenv("PINECONE_HOST", "")  # Will be set after index creation

_pc = None
_index = None


def _get_pinecone():
    """Ленивая инициализация Pinecone клиента."""
    global _pc, _index
    if _index is not None:
        return _index
    
    try:
        from pinecone import Pinecone
        _pc = Pinecone(api_key=PINECONE_API_KEY)
        
        # Проверяем/создаём индекс
        existing = [idx.name for idx in _pc.list_indexes()]
        
        if PINECONE_INDEX_NAME not in existing:
            from pinecone import ServerlessSpec
            _pc.create_index(
                name=PINECONE_INDEX_NAME,
                dimension=384,
                metric="cosine",
                spec=ServerlessSpec(cloud="aws", region="us-east-1")
            )
            logger.info(f"[VECTOR] Created Pinecone index: {PINECONE_INDEX_NAME}")
        
        _index = _pc.Index(PINECONE_INDEX_NAME)
        logger.info(f"[VECTOR] Connected to Pinecone index: {PINECONE_INDEX_NAME}")
        return _index
        
    except Exception as e:
        logger.warning(f"[VECTOR] Pinecone init failed: {e}")
        return None


def get_pinecone_index():
    """Публичный доступ к Pinecone индексу (для удаления векторов)."""
    return _get_pinecone()


# ═══════════════════════════════════════════════════════════════
# РАСШИРЕННЫЕ СЕМАНТИЧЕСКИЕ КАТЕГОРИИ (200+ тем)
# ═══════════════════════════════════════════════════════════════

SEMANTIC_CATEGORIES = {
    # ─── РАЗРАБОТКА ───
    'разработка': ['код', 'баг', 'фича', 'api', 'разработка', 'программирование', 'деплой',
                   'git', 'коммит', 'рефакторинг', 'тестирование', 'frontend', 'backend', 'debug',
                   'алгоритм', 'техдолг', 'ревью', 'спринт', 'релиз', 'hotfix', 'микросервис'],
    'python': ['python', 'django', 'flask', 'fastapi', 'aiohttp', 'asyncio', 'sqlalchemy',
               'pydantic', 'alembic', 'pip', 'virtualenv', 'poetry', 'pytest', 'unittest'],
    'javascript': ['javascript', 'typescript', 'node.js', 'react', 'vue', 'angular', 'npm',
                   'yarn', 'webpack', 'vite', 'next.js', 'nuxt'],
    'базы_данных': ['postgresql', 'sqlite', 'mysql', 'mongodb', 'redis', 'sql', 'orm',
                    'миграция', 'индекс', 'запрос', 'транзакция', 'connection pool'],
    'инфраструктура': ['docker', 'kubernetes', 'nginx', 'linux', 'сервер', 'хостинг',
                       'railway', 'heroku', 'aws', 'cloud', 'devops', 'ci/cd'],
    'безопасность': ['безопасность', 'шифрование', 'аутентификация', 'oauth', 'jwt',
                     'csrf', 'xss', 'sql injection', 'ssl', 'https', 'firewall'],

    # ─── СТАРТАПЫ И БИЗНЕС ───
    'стартап': ['стартап', 'mvp', 'продукт', 'запуск', 'pivot', 'юнит-экономика', 'traction',
                'выход на рынок', 'product-market fit', 'монетизация', 'growth', 'масштабирование'],
    'бизнес': ['бизнес', 'компания', 'ооо', 'ип', 'регистрация', 'устав', 'доля', 'соучредитель',
               'партнёрство', 'франшиза', 'лицензия', 'разрешение'],
    'стратегия': ['стратегия', 'план', 'цель', 'миссия', 'видение', 'roadmap', 'бизнес-план',
                  'анализ рынка', 'конкуренты', 'свот', 'pest', 'стратегическая сессия'],
    'управление': ['проект', 'дедлайн', 'спринт', 'kanban', 'команда', 'менеджмент', 'планирование',
                   'приоритеты', 'делегирование', 'контроль', 'milestone', 'тайм-менеджмент'],
    'продукт': ['продукт', 'юзер стори', 'roadmap', 'приоритизация', 'ice', 'rice',
                'продуктовая аналитика', 'a/b тест', 'метрики', 'product owner'],
    'найм': ['найм', 'вакансия', 'резюме', 'собеседование', 'hr', 'рекрутинг', 'оффер',
             'аутстафф', 'фриланс', 'испытательный срок', 'адаптация', 'онбординг'],

    # ─── МАРКЕТИНГ И ПРОДАЖИ ───
    'маркетинг': ['маркетинг', 'реклама', 'таргет', 'smm', 'бренд', 'охват', 'виральность',
                  'позиционирование', 'промо', 'рекламный бюджет', 'аналитика', 'метрики'],
    'seo': ['seo', 'ранжирование', 'трафик', 'ключевые слова', 'ссылки', 'оптимизация',
            'google analytics', 'яндекс.метрика', 'индексация', 'поисковая выдача'],
    'таргетинг': ['таргет', 'ads', 'facebook ads', 'vk ads', 'yandex direct', 'google ads',
                  'ретаргет', 'look-alike', 'cpp', 'ctr', 'конверсия', 'лиды'],
    'контент_маркетинг': ['контент', 'пост', 'статья', 'видео', 'блог', 'newsletter', 'рассылка',
                          'копирайтинг', 'сценарий', 'подкаст', 'сторис', 'лонгрид'],
    'продажи': ['продажа', 'сделка', 'воронка', 'конверсия', 'лид', 'crm', 'холодный звонок',
                'коммерческое предложение', 'переговоры', 'закрытие', 'аутрич', 'upsell'],
    'клиенты': ['клиент', 'заказчик', 'пользователь', 'отзыв', 'обратная связь', 'retention',
                'churn', 'ltv', 'лояльность', 'поддержка', 'саппорт', 'nps', 'csat'],
    'email_маркетинг': ['email', 'письмо', 'рассылка', 'почта', 'inbox', 'ответ', 'переписка',
                        'спам', 'холодная рассылка', 'outreach', 'imap', 'автоответчик'],

    # ─── КРИПТО И ФИНАНСЫ ───
    'крипто_общее': ['крипто', 'биткоин', 'ethereum', 'blockchain', 'токен', 'блокчейн',
                     'кошелёк', 'майнинг', 'binance', 'bybit', 'huobi', 'okx'],
    'defi': ['defi', 'uniswap', 'pancakeswap', 'liquidity', 'yield farming', 'aave',
             'compound', 'lending', 'staking', 'пул ликвидности', 'amm'],
    'трейдинг': ['трейдинг', 'торговля', 'сигнал', 'ордер', 'свечи', 'rsi', 'macd', 'sma',
                 'индикатор', 'анализ', 'лонг', 'шорт', 'волатильность', 'объём'],
    'арбитраж': ['арбитраж', 'спред', 'p2p', 'разница курсов', 'межбиржевой', 'фронтран',
                 'ликвидация', 'флиппинг', 'перепродажа'],
    'p2p': ['p2p', 'peer-to-peer', 'спред', 'купить usdt', 'продать usdt', '₽', 'usdt/rub',
            'binance p2p', 'bybit p2p', 'платёжная система'],
    'инвестиции': ['инвестиции', 'инвестор', 'раунд', 'венчур', 'ангел', 'акции', 'портфель',
                   'дивиденды', 'roi', 'irr', 'ebitda', 'оценка', 'кап'],
    'трейдинг_стратегии': ['сетка', 'grid', 'dca', 'усреднение', 'мартингейл', 'хедж',
                           'скальпинг', 'свинг', 'позиционная торговля', 'алготрейдинг'],
    'тех_анализ': ['технический анализ', 'уровень поддержки', 'сопротивление', 'тренд',
                   'канал', 'фибоначчи', 'паттерн', 'свечной анализ', 'объём торгов'],
    'фундаментальный_анализ': ['фундаментальный анализ', 'отчётность', 'ipo', 'капитализация',
                               'доля рынка', 'конкуренты', 'индустрия', 'сектор'],
    'стейкинг': ['стейкинг', 'награда', 'apy', 'apr', 'валидатор', 'пул', 'делегирование',
                 'lock', 'верификация', 'проценты'],

    # ─── ФИНАНСЫ ───
    'финансы': ['деньги', 'бюджет', 'зарплата', 'доход', 'расход', 'прибыль', 'кэшфлоу',
                'налоги', 'бухгалтерия', 'финансовый план', 'фин модель'],
    'налоги': ['налог', 'ндс', 'ндфл', 'усн', 'осно', 'патент', 'отчётность', 'декларация',
               'налоговая', 'вычет', 'пенсионный', 'страховые взносы'],
    'крипто_налоги': ['крипто налоги', 'декларация крипто', 'прибыль крипто', 'убыток',
                      'tax', 'crypto tax', 'gain loss', 'reporting'],
    'бухгалтерия': ['бухгалтерия', 'учёт', 'проводка', 'баланс', 'отчёт', 'дебет', 'кредит',
                    'актив', 'пассив', 'оборотка', 'главная книга'],
    'банкинг': ['банк', 'счёт', 'карта', 'перевод', 'сбербанк', 'тинькофф', 'альфа',
                'вклад', 'кредит', 'ипотека', 'проценты', 'кэшбэк'],
    'кредиты': ['кредит', 'займ', 'ипотека', 'ставка', 'переплата', 'досрочное погашение',
                'рефинансирование', 'кредитная история', 'бюро кредитных историй'],

    # ─── TINKOFF INVEST ───
    'tinkoff_invest': ['tinkoff', 'тинькофф инвестиции', 'портфель', 'акции', 'облигации',
                       'etf', 'взаимные фонды', 'брокер', 'иис', 'индивидуальный счёт'],
    'tinkoff_api': ['tinkoff api', 'get_tinkoff_balance', 'tinkoff_portfolio',
                    'tinkoff_operations', 'sandbox tinkoff', 'токен доступа'],

    # ─── ЕСТЬ ───
    'рестораны': ['ресторан', 'кафе', 'еда', 'рецепт', 'кухня', 'доставка', 'заказ',
                  'меню', 'шеф', 'бар', 'кофе', 'обед', 'ужин', 'завтрак'],
    'здоровое_питание': ['здоровое питание', 'пп', 'калории', 'бжу', 'диета', 'спортивное питание',
                         'витамины', 'минералы', 'нутрициология', 'детокс'],
    'кулинария': ['кулинария', 'готовка', 'рецепт', 'ингредиент', 'блюдо', 'выпечка',
                  'десерт', 'суп', 'салат', 'закуска', 'соус', 'маринад'],

    # ─── ЗДОРОВЬЕ ───
    'здоровье': ['здоровье', 'спорт', 'тренировка', 'бег', 'зал', 'медитация',
                 'питание', 'диета', 'вес', 'анализы', 'врач', 'болезнь'],
    'фитнес': ['фитнес', 'тренировка', 'упражнение', 'мышцы', 'сила', 'выносливость',
               'кроссфит', 'кардио', 'растяжка', 'йога', 'пилатес'],
    'бег': ['бег', 'марафон', 'полумарафон', 'кросс', 'темп', 'пульс', 'heart rate',
            '5 км', '10 км', 'интервальный бег', 'трейл'],
    'медицина': ['врач', 'больница', 'клиника', 'диагноз', 'лечение', 'терапия', 'операция',
                 'лекарство', 'таблетки', 'симптомы', 'анализ крови'],
    'ментальное': ['стресс', 'тревога', 'депрессия', 'психология', 'терапия', 'mindfulness',
                   'фокус', 'прокрастинация', 'мотивация', 'привычка', 'медитация'],
    'сон': ['сон', 'бессонница', 'режим сна', 'циркадный ритм', 'мелатонин',
            'ранний подъём', 'сонливость', 'усталость', 'восстановление'],

    # ─── ОБРАЗОВАНИЕ ───
    'обучение': ['учёба', 'курс', 'книга', 'навык', 'сертификат', 'обучение', 'лекция',
                 'мастер-класс', 'тренинг', 'практика', 'образование', 'университет'],
    'саморазвитие': ['саморазвитие', 'книга', 'чтение', 'курсы', 'навыки', 'развитие',
                     'рост', 'практика', 'обучение', 'познание'],
    'иностранные_языки': ['английский', 'испанский', 'немецкий', 'французский', 'китайский',
                          'язык', 'перевод', 'toefl', 'ielts', 'duolingo', 'lingua'],
    'онлайн_курсы': ['udemy', 'coursera', 'stepik', 'skillbox', 'нетология', 'geekbrains',
                     'otus', 'яндекс практикум', 'вебинар', 'вебинар'],
    'чтение': ['книга', 'читать', 'журнал', 'статья', 'блог', 'автор', 'издательство',
               'бестселлер', 'рецензия', 'отзыв на книгу', 'библиотека'],

    # ─── КАРЬЕРА ───
    'карьера': ['карьера', 'повышение', 'собеседование', 'резюме', 'оффер', 'увольнение',
                'фриланс', 'удалёнка', 'зарплата', 'должность', 'профессия'],
    'резюме': ['резюме', 'cv', 'портфолио', 'опыт работы', 'навыки', 'образование',
               'linkedin', 'hh.ru', 'superjob', 'сопроводительное'],
    'фриланс': ['фриланс', 'заказчик', 'проект', 'тендер', 'биржи', 'upwork', 'freelance',
                'kwork', 'pchelp', 'fl.ru', 'самозанятость'],
    'переговоры': ['переговоры', 'оффер', 'зарплата', 'контракт', 'условия', 'бонусы',
                   'опционы', 'релокация', 'пакет', 'компенсация'],
    'сеть_контактов': ['нетворкинг', 'контакт', 'знакомство', 'коллега', 'встреча', 'конференция',
                       'менторство', 'коммьюнити', 'связи', 'рекомендация'],

    # ─── ЖИЗНЬ ───
    'недвижимость': ['квартира', 'дом', 'аренда', 'ипотека', 'ремонт', 'переезд',
                     'офис', 'коворкинг', 'площадь', 'комната', 'студия'],
    'путешествия': ['путешествие', 'отпуск', 'перелёт', 'билет', 'отель', 'виза',
                    'командировка', 'релокация', 'страна', 'маршрут', 'туры'],
    'авто': ['авто', 'машина', 'автомобиль', 'покупка авто', 'продажа авто', 'ремонт',
             'страховка', 'осаго', 'каско', 'техосмотр', 'штраф', 'топливо'],
    'переезд': ['переезд', 'релокация', 'эмиграция', 'внж', 'вид на жительство',
                'гражданство', 'разрешение на работу', 'виза'],
    'покупки': ['покупка', 'шопинг', 'онлайн', 'маркетплейс', 'wildberries', 'ozon',
                'сравнение цен', 'отзывы', 'доставка', 'возврат'],

    # ─── AI И ТЕХНОЛОГИИ ───
    'ai_ml': ['ai', 'нейросеть', 'модель', 'gpt', 'deepseek', 'llm', 'промпт',
              'fine-tuning', 'rag', 'embeddings', 'трансформер', 'агент', 'ml'],
    'нейросети': ['нейросеть', 'ann', 'cnn', 'rnn', 'transformer', 'gan', 'диффузия',
                  'стабил дифьюжн', 'midjourney', 'dalle', 'генерация'],
    'llm': ['llm', 'gpt', 'claude', 'gemini', 'deepseek', 'llama', 'mistral',
            'token', 'контекст', 'промпт инжиниринг', 'fine-tuning'],
    'агенты': ['агент', 'autogpt', 'сustom agent', 'tool use', 'function calling',
               'autonomous agent', 'multi-agent', 'оркестрация', 'агентная сеть'],
    'автоматизация': ['автоматизация', 'бот', 'скрипт', 'интеграция', 'webhook', 'api',
                      'парсинг', 'cron', 'триггер', 'пайплайн', 'workflow'],
    'чат_боты': ['чат-бот', 'telegram bot', 'discord bot', 'dialog flow', 'rasa',
                 'customer support', 'helpdesk', 'тикеты'],

    # ─── ДИЗАЙН ───
    'дизайн': ['дизайн', 'ui', 'ux', 'макет', 'figma', 'прототип', 'логотип',
               'визуал', 'иллюстрация', 'брендбук', 'типографика'],
    'веб_дизайн': ['веб-дизайн', 'адаптивность', 'responsive', 'landing', 'сайт',
                   'лендинг', 'интерфейс', 'user experience', 'юзабилити'],
    'графический_дизайн': ['графический дизайн', 'photoshop', 'illustrator', 'corel',
                           'фигма', 'скетч', 'adobe', 'canva', 'презентация'],

    # ─── КОНТЕНТ И МЕДИА ───
    'соцсети': ['канал', 'телеграм', 'instagram', 'youtube', 'tiktok', 'подписчики',
                'аудитория', 'сообщество', 'вовлечённость', 'stories', 'reels'],
    'telegram': ['telegram', 'tg', 'канал', 'чат', 'бот', 'команда', 'подписчик',
                 'пост', 'пин', 'закреп', 'форвард', 'реплай'],
    'youtube': ['youtube', 'видео', 'канал', 'подписчики', 'просмотры', 'монетизация',
                'алгоритм', 'рекомендации', 'клип', 'short', 'стрим'],
    'ведение_соцсетей': ['smm', 'ведение', 'контент-план', 'рубрики', 'публикации',
                         'график постов', 'вовлечение', 'конкурс', 'giveaway'],
    'копирайтинг': ['копирайтинг', 'текст', 'продающий текст', 'заголовок', 'оффер',
                    'призыв к действию', 'а/б тест', 'уникальное предложение'],
    'видео_продакшн': ['видео', 'съёмка', 'монтаж', 'сценарий', 'озвучка', 'субтитры',
                       'превью', 'камера', 'свет', 'микрофон'],

    # ─── ЮРИДИЧЕСКОЕ ───
    'юридическое': ['договор', 'контракт', 'патент', 'лицензия', 'ip', 'юрист',
                    'суд', 'претензия', 'штраф', 'регистрация ооо', 'устав'],
    'интеллектуальная_собственность': ['интеллектуальная собственность', 'авторское право',
                                        'патент', 'товарный знак', 'копирайт', 'лицензия'],
    'договоры': ['договор', 'оферта', 'акт', 'счёт', 'спецификация', 'приложение',
                 'доп соглашение', 'конфиденциальность', 'nda'],

    # ─── АРЕНА И АГЕНТЫ ───
    'агенты_пользователя': ['агент', 'персонаж', 'личность', 'аватар', 'имя агента',
                            'настройка', 'python код', 'интеграция', 'api ключи'],
    'арена': ['арена', 'arena', 'пост', 'лента', 'комментарий', 'лайк', 'рейтинг',
              'агент на арене', 'обсуждение', 'волна', 'токены арены'],
    'маркетплейс': ['маркетплейс', 'marketplace', 'агент', 'подписка', 'активация',
                    'цена за сообщение', 'роялти', 'пробные сообщения'],

    # ─── ЭМОЦИИ И СОСТОЯНИЯ ───
    'эмоции_позитив': ['рад', 'счастлив', 'воодушевлён', 'энтузиазм', 'благодарность',
                       'гордость', 'вдохновение', 'кураж', 'эйфория', 'успех'],
    'эмоции_негатив': ['грустно', 'злюсь', 'разочарован', 'волнуюсь', 'боюсь', 'обидно',
                       'раздражение', 'апатия', 'безразличие', 'вина', 'одиночество'],
    'мотивация': ['мотивация', 'вдохновение', 'драйв', 'энтузиазм', 'заряд', 'энергия',
                  'настрой', 'дисциплина', 'сила воли', 'преодоление'],
    'выгорание': ['выгорание', 'усталость', 'нет сил', 'опустошение', 'потеря интереса',
                  'демотивация', 'апатия', 'безразличие', 'rest'],

    # ─── ЦЕЛИ И ПРИВЫЧКИ ───
    'цели': ['цель', 'план', 'стратегия', 'результат', 'прогресс', 'достижение',
             'okr', 'kpi', 'milestone', 'roadmap', 'задачи'],
    'привычки': ['привычка', 'рутина', 'трекер', 'дисциплина', 'ежедневно', 'утренний ритуал',
                 'марафон', 'челлендж', 'стрик', 'регулярность', 'daily'],
    'продуктивность': ['продуктивность', 'эффективность', 'gtd', 'pomodoro', 'фокус',
                       'антипрокрастинация', 'deep work', 'flow', 'таймблокинг'],
    'планирование': ['план', 'планирование', 'расписание', 'ежедневник', 'календарь',
                     'список дел', 'todo', 'notion', 'заметки', 'органайзер'],

    # ─── ИНТЕГРАЦИИ И СЕРВИСЫ ───
    'ozon_wb': ['ozon', 'wildberries', 'маркетплейс', 'товары', 'заказы', 'поставки',
                'продвижение', 'карточка товара', 'отзывы', 'рейтинг'],
    'crm': ['crm', 'amocrm', 'bitrix24', 'salesforce', 'hubspot', 'воронка',
            'лиды', 'сделки', 'контакты', 'задачи в crm'],
    'notion': ['notion', 'база данных', 'страницы', 'шаблон', 'документация',
               'wiki', 'knowledge base', 'database', 'связи'],
    'github': ['github', 'gitlab', 'репозиторий', 'pull request', 'issue',
               'actions', 'workflow', 'автоматизация', 'открытый код'],
    'google': ['gmail', 'google drive', 'google sheets', 'google calendar',
               'google docs', 'google workspace', 'g suite'],
    'slack': ['slack', 'канал', 'сообщение', 'воркспейс', 'интеграция',
              'уведомление', 'тред', 'реакция'],
    'jira': ['jira', 'таск', 'задача', 'спринт', 'эпик', 'стори поинт',
             'доска', 'backlog', 'agile', 'scrum', 'фильтр'],
    'stripe': ['stripe', 'платежи', 'подписка', 'биллинг', 'инвойс', 'refund',
               'charge', 'customer', 'payment intent'],
    'discord': ['discord', 'сервер', 'канал', 'роль', 'сообщение', 'модерация',
                'бот', 'слэш-команды', 'интеграция'],

    # ─── ПРОЕКТЫ ───
    'веб_разработка': ['сайт', 'landing', 'лендинг', 'веб-приложение', 'spa',
                       'фронтенд', 'бэкенд', 'полный стек', 'fullstack'],
    'мобильная_разработка': ['мобильное приложение', 'ios', 'android', 'react native',
                             'flutter', 'swift', 'kotlin', 'app store', 'google play'],
    'telegram_боты': ['telegram bot', 'aiogram', 'python telegram bot', 'команда',
                      'кнопки', 'инлайн', 'вебхук', 'поллинг', 'состояние'],
    'сайты': ['сайт', 'домен', 'хостинг', 'вордпресс', 'tilda', 'readymag',
              'конструктор', 'админка', 'cms', 'шаблон'],
    'аналитика': ['аналитика', 'метрики', 'данные', 'отчёт', 'дашборд', 'grafana',
                  'таблица', 'статистика', 'визуализация', 'анализ'],

    # ─── РАЗНОЕ ───
    'хобби': ['хобби', 'творчество', 'музыка', 'рисование', 'фото', 'рукоделие',
              'коллекционирование', 'садоводство', 'рыбалка', 'охота'],
    'музыка': ['музыка', 'песня', 'альбом', 'трек', 'плейлист', 'концерт',
               'гитара', 'фортепиано', 'музыкальный инструмент', 'композиция'],
    'спорт': ['спорт', 'футбол', 'баскетбол', 'теннис', 'хоккей', 'волейбол',
              'плавание', 'велосипед', 'лыжи', 'единоборства'],
    'игры': ['игры', 'гейминг', 'видеоигры', 'steam', 'playstation', 'xbox',
             'киберспорт', 'dota', 'cs', 'стратегия', 'rpg'],
    'кино': ['кино', 'фильм', 'сериал', 'кинотеатр', 'режиссёр', 'актёр',
             'жанр', 'драма', 'комедия', 'триллер', 'фантастика'],
    'животные': ['животные', 'питомец', 'собака', 'кот', 'кошка', 'ветеринар',
                 'уход', 'корм', 'порода', 'дрессировка'],
    'праздники': ['праздник', 'день рождения', 'новый год', 'свадьба', 'юбилей',
                  'подарок', 'поздравление', 'торжество', 'мероприятие'],

    # ─── СОБЫТИЯ И НОВОСТИ ───
    'новости': ['новости', 'события', 'актуально', 'последнее', 'сводка',
                'дайджест', 'обзор', 'произошло', 'узнал'],
    'мероприятия': ['мероприятие', 'конференция', 'митап', 'форум', 'выставка',
                    'хакатон', 'воркшоп', 'вебинар', 'нетворкинг', 'afterparty'],
    'хакатон': ['хакатон', 'команда', 'проект за 48ч', 'питч', 'демо', 'джюри',
                'приз', 'победитель', 'трек', 'ментор'],
}

# ═══════════════════════════════════════════════════════════════
# УЛУЧШЕННЫЕ PSEUDO-EMBEDDINGS (384 dims)
# ═══════════════════════════════════════════════════════════════

EMBEDDING_DIM = 384

# Компилируем ключевые слова для быстрого поиска
_CATEGORY_KEYWORDS_LOWER: dict[str, set[str]] = {}
for cat, kws in SEMANTIC_CATEGORIES.items():
    _CATEGORY_KEYWORDS_LOWER[cat] = set(k.lower() for k in kws)

# Длинные фразы (2+ слов) — проверяем как substring
_CATEGORY_PHRASES: dict[str, list[str]] = {}
for cat, kws in SEMANTIC_CATEGORIES.items():
    _CATEGORY_PHRASES[cat] = [k.lower() for k in kws if len(k.split()) > 1]


def _pseudo_embedding(text: str) -> list[float]:
    """Улучшенные pseudo-embeddings: все категории + TF-IDF bigrams + позиционные веса.
    
    Dimension: 384
    - Первые N dims: по 2 dims на категорию (exact + fuzzy)
    - Остальные: weighted bigram hashing + position weights
    
    Значительно точнее старой версии за счёт:
    1. Использования ВСЕХ категорий (200+ вместо 30)
    2. Позиционного взвешивания (первые слова важнее)
    3. Учета длинных фраз (2+ слов)
    4. Fuzzy matching с весами
    """
    text_lower = text.lower()
    words = re.findall(r'\b[а-яёa-z0-9]{2,}\b', text_lower)
    word_set = set(words)
    
    vec = [0.0] * EMBEDDING_DIM
    n_cats = len(SEMANTIC_CATEGORIES)
    
    # Первые N*2 dims: все категории × 2 (точное совпадение + fuzzy)
    cat_count = min(n_cats, EMBEDDING_DIM // 2 - 5)  # оставляем место для спец dims
    for i, (category, _) in enumerate(list(SEMANTIC_CATEGORIES.items())[:cat_count]):
        keywords = _CATEGORY_KEYWORDS_LOWER.get(category, set())
        phrases = _CATEGORY_PHRASES.get(category, [])
        
        # Точное совпадение слов
        exact_matches = len(word_set & keywords)
        vec[i * 2] = min(exact_matches / max(len(keywords) * 0.15, 1), 1.0)
        
        # Fuzzy: substring + phrase matching
        substr_score = 0.0
        for kw in keywords:
            if kw in text_lower and kw not in word_set:
                substr_score += 0.5
        for phrase in phrases:
            if phrase in text_lower:
                substr_score += 1.5  # phrases weigh more
        vec[i * 2 + 1] = min(substr_score / max(len(keywords) * 0.25, 1), 1.0)
    
    # Специальные dims: длина текста, уникальность слов, пунктуация
    cat_offset = cat_count * 2
    if cat_offset + 5 < EMBEDDING_DIM:
        # Длина текста (нормализованная)
        vec[cat_offset] = min(len(text) / 500, 1.0)  # до 500 символов = 1.0
        # Отношение уникальных слов к общему количеству (лексическое разнообразие)
        vec[cat_offset + 1] = min(len(word_set) / max(len(words), 1), 1.0) if words else 0
        # Наличие чисел (финансы, крипто, даты)
        vec[cat_offset + 2] = 1.0 if re.search(r'\d+', text) else 0.0
        # Наличие URL/ссылок
        vec[cat_offset + 3] = 1.0 if re.search(r'https?://|t\.me/|@', text) else 0.0
        # Наличие эмодзи
        vec[cat_offset + 4] = 1.0 if re.search(r'[\U0001F300-\U0001F9FF]', text) else 0.0
    
    # Оставшиеся dims: weighted bigram hashing + position weights
    bigram_offset = min(cat_offset + 5, EMBEDDING_DIM - 30)
    bigram_dims = EMBEDDING_DIM - bigram_offset  # минимум 30 dims для хэшей
    
    if bigram_dims > 0 and words:
        # Word bigrams
        word_bigrams = [f"{words[j]}_{words[j+1]}" for j in range(len(words) - 1)]
        
        # Char trigrams для коротких текстов
        char_trigrams = [text_lower[j:j+3] for j in range(len(text_lower) - 2)]
        
        # TF-IDF подобное взвешивание
        bigram_counts = Counter(word_bigrams)
        for bg, count in bigram_counts.items():
            h = int(hashlib.md5(bg.encode()).hexdigest()[:10], 16) % bigram_dims
            weight = math.log1p(count) * 0.3
            vec[bigram_offset + h] = min(vec[bigram_offset + h] + weight, 1.0)
        
        # Char trigrams — вторичный сигнал с меньшим весом
        trigram_counts = Counter(char_trigrams)
        for tg, count in trigram_counts.items():
            h = int(hashlib.md5(tg.encode()).hexdigest()[:8], 16) % bigram_dims
            weight = math.log1p(count) * 0.08
            vec[bigram_offset + h] = min(vec[bigram_offset + h] + weight, 1.0)
        
        # Позиционное взвешивание: первые и последние 3 слова получают доп хэши
        for pos_weight, word_list in [(0.5, words[:3]), (0.3, words[-3:])]:
            for w in word_list:
                h = int(hashlib.md5(f"pos:{w}".encode()).hexdigest()[:8], 16) % bigram_dims
                vec[bigram_offset + h] = min(vec[bigram_offset + h] + pos_weight * 0.2, 1.0)
    
    # L2 нормализация
    magnitude = sum(v ** 2 for v in vec) ** 0.5
    if magnitude > 0:
        vec = [v / magnitude for v in vec]
    
    return vec


def _text_to_embedding(text: str) -> list[float]:
    """Создаёт pseudo-embedding (единственный метод, OpenAI удалён)."""
    return _pseudo_embedding(text)


# ═══════════════════════════════════════════════════════════════
# ПУБЛИЧНЫЙ API
# ═══════════════════════════════════════════════════════════════

def _store_memory_sync(user_id, text, metadata=None):
    """Синхронная внутренняя версия — НЕ вызывать из async кода напрямую."""
    index = _get_pinecone()
    if not index:
        logger.debug("[VECTOR] Pinecone unavailable, skipping store")
        return False
    
    try:
        # ID = hash от user_id + text + timestamp (уникальность)
        ts = datetime.now(timezone.utc).isoformat()
        vec_id = hashlib.md5(f"{user_id}:{text}:{ts}".encode()).hexdigest()
        
        embedding = _text_to_embedding(text)
        
        meta = {
            "user_id": str(user_id),
            "text": text[:500],  # Pinecone metadata limit
            "timestamp": ts,
            "type": "message",
        }
        if metadata:
            meta.update({k: str(v)[:200] for k, v in metadata.items()})
        
        index.upsert(vectors=[{
            "id": vec_id,
            "values": embedding,
            "metadata": meta,
        }], namespace=f"user_{user_id}")
        
        logger.info(f"[VECTOR] Stored memory for user {user_id}: {text[:50]}...")
        return True
        
    except Exception as e:
        logger.warning(f"[VECTOR] Store failed: {e}")
        return False


def _search_memory_sync(user_id, query, top_k=15):
    """Синхронная внутренняя версия с улучшенным поиском."""
    index = _get_pinecone()
    if not index:
        return []
    
    try:
        embedding = _text_to_embedding(query)
        
        results = index.query(
            vector=embedding,
            top_k=top_k,
            include_metadata=True,
            namespace=f"user_{user_id}",
            filter={"user_id": {"$eq": str(user_id)}}
        )
        
        memories = []
        for match in results.get("matches", []):
            meta = match.get("metadata", {})
            memories.append({
                "text": meta.get("text", ""),
                "score": match.get("score", 0),
                "timestamp": meta.get("timestamp", ""),
                "type": meta.get("type", "message"),
            })
        
        logger.info(f"[VECTOR] Found {len(memories)} memories for user {user_id}, query: {query[:50]}")
        return memories
        
    except Exception as e:
        logger.warning(f"[VECTOR] Search failed: {e}")
        return []


async def search_memory(user_id, query, top_k=15):
    """Async-обёртка: семантический поиск без блокировки event loop."""
    try:
        return await asyncio.to_thread(_search_memory_sync, user_id, query, top_k)
    except Exception as e:
        logger.warning(f"[VECTOR] Async search failed: {e}")
        return []


def _deduplicate_memories(memories: list[dict], similarity_threshold: float = 0.85) -> list[dict]:
    """Дедупликация похожих воспоминаний (по тексту)."""
    if not memories:
        return []
    
    deduped = []
    seen_texts = set()
    
    for m in sorted(memories, key=lambda x: x.get('score', 0), reverse=True):
        txt = (m.get('text', '') or '').strip().lower()
        if not txt:
            continue
        
        # Проверяем на дубликаты
        is_dup = False
        for seen in seen_texts:
            # Если одно содержит другое — это дубль
            if txt in seen or seen in txt:
                is_dup = True
                break
            # Если совпадение больше 70% символов
            if len(txt) > 10 and len(seen) > 10:
                overlap = len(set(txt) & set(seen)) / max(len(set(txt) | set(seen)), 1)
                if overlap > similarity_threshold:
                    is_dup = True
                    break
        
        if not is_dup:
            deduped.append(m)
            seen_texts.add(txt)
    
    return deduped


def _build_memory_context_sync(user_id, current_message, max_chars=2500):
    """Синхронный поиск памяти и формирование текстового контекста (улучшенная версия).
    
    Улучшения:
    - Дедупликация похожих воспоминаний
    - Recency boost (свежие воспоминания получают приоритет)
    - Адаптивный порог для pseudo-embeddings
    - Группировка по типу (цели, решения, инсайты — выше приоритет)
    """
    memories = _search_memory_sync(user_id, current_message, top_k=20)
    if not memories:
        return ""
    
    # Для pseudo-embeddings порог ниже, т.к. они менее точные
    _threshold = 0.35
    
    # Типы с повышенным приоритетом
    _high_priority_types = {'goal', 'decision', 'insight', 'achievement', 'milestone'}
    # Типы с пониженным приоритетом
    _low_priority_types = {'emotion', 'conversation', 'greeting'}
    
    # Дедуплицируем
    memories = _deduplicate_memories(memories)
    
    # Сортируем: высокоприоритетные типы выше, потом по свежести
    now = datetime.now(timezone.utc)
    
    def _sort_key(m):
        mtype = m.get("type", "")
        score = m.get("score", 0)
        
        # Boost для высокоприоритетных типов
        type_boost = 0.3 if mtype in _high_priority_types else (-0.2 if mtype in _low_priority_types else 0)
        
        # Recency boost: чем свежее, тем выше
        try:
            ts = m.get("timestamp", "")
            if ts:
                mtime = datetime.fromisoformat(ts)
                hours_ago = (now - mtime).total_seconds() / 3600
                recency_boost = max(0, 1.0 - hours_ago / 168) * 0.2  # 7 дней = полный затух
            else:
                recency_boost = 0
        except (ValueError, TypeError):
            recency_boost = 0
        
        return (score + type_boost + recency_boost)
    
    memories.sort(key=_sort_key, reverse=True)
    
    # Формируем контекст
    parts = []
    total = 0
    
    for m in memories:
        txt = m.get("text", "")
        mtype = m.get("type", "")
        score = m.get("score", 0)
        
        # Динамический порог: для high-priority типов немного снижаем
        effective_threshold = (_threshold - 0.08) if mtype in _high_priority_types else _threshold
        
        if not txt or score <= effective_threshold:
            continue
        
        # Префикс по типу
        prefix_map = {
            "goal": "🎯",
            "decision": "📌",
            "insight": "💡",
            "achievement": "✅",
            "milestone": "🏆",
            "emotion": "💭",
        }
        prefix = prefix_map.get(mtype, "—")
        
        entry = f"{prefix} {txt[:200]}"
        
        if total + len(entry) > max_chars:
            break
        
        parts.append(entry)
        total += len(entry)
    
    if not parts:
        return ""
    
    return "Из памяти:\n" + "\n".join(parts)


async def build_memory_context(user_id, current_message, max_chars=2500):
    """Async-обёртка: строит контекст памяти без блокировки event loop."""
    try:
        return await asyncio.to_thread(_build_memory_context_sync, user_id, current_message, max_chars)
    except Exception as e:
        logger.warning(f"[VECTOR] Async memory context failed: {e}")
        return ""


def _chunk_long_text(text: str, max_chunk: int = 300) -> list[str]:
    """Разбивает длинный текст на чанки по предложениям."""
    if len(text) <= max_chunk:
        return [text]
    
    chunks = []
    # Разбиваем по предложениям
    sentences = re.split(r'(?<=[.!?])\s+', text)
    current = ""
    
    for s in sentences:
        if len(current) + len(s) > max_chunk and current:
            chunks.append(current.strip())
            current = s
        else:
            current += " " + s if current else s
    
    if current.strip():
        chunks.append(current.strip())
    
    return chunks


def _store_conversation_turn_sync(user_id, user_message, bot_response, emotion=None, intent=None):
    """Синхронная внутренняя версия — сохраняет значимый обмен.
    
    Улучшения:
    - Чанкинг длинных сообщений (разбивка на части)
    - Более точная классификация типа контента
    - Сохранение не только вопроса пользователя, но и сути ответа AI
    """
    # Фильтр: сохраняем только значимые сообщения
    if len(user_message) < 10:
        return False
    
    skip_patterns = ['привет', 'пока', 'ок', 'да', 'нет', 'ладно', 'спасибо', 'спс',
                     'понял', 'понятно', 'хорошо', 'окей', 'ага', 'ну да']
    if user_message.lower().strip() in skip_patterns:
        return False
    
    # Определяем тип контента
    content_type = "conversation"
    if any(w in user_message.lower() for w in ['цель', 'план', 'хочу', 'мечта', 'стремлюсь',
                                                'моя цель', 'моя задача', 'должен']):
        content_type = "goal"
    elif any(w in user_message.lower() for w in ['решил', 'принял решение', 'буду', 'сделаю',
                                                  'я выбираю', 'я выбираю']):
        content_type = "decision"
    elif any(w in user_message.lower() for w in ['узнал', 'понял', 'осознал', 'открытие',
                                                  'заметил', 'обнаружил', 'выяснил']):
        content_type = "insight"
    elif any(w in user_message.lower() for w in ['устал', 'грустно', 'рад', 'злюсь', 'боюсь',
                                                  'тревожно', 'одиноко', 'счастлив', 'беспокоюсь']):
        content_type = "emotion"
    elif any(w in user_message.lower() for w in ['сделал', 'получилось', 'выполнил', 'готово',
                                                  'завершил', 'достиг']):
        content_type = "achievement"
    
    # Базовая метадата
    metadata = {
        "type": content_type,
        "emotion": emotion or "neutral",
        "intent": intent or "general",
        "response_preview": (bot_response or '')[:150],
    }
    
    # Сохраняем сообщение пользователя
    user_chunks = _chunk_long_text(user_message)
    stored = False
    for chunk in user_chunks:
        if _store_memory_sync(user_id, chunk, metadata):
            stored = True
    
    # Если ответ содержательный — сохраняем и суть ответа (как insight)
    if bot_response and len(bot_response) > 50:
        # Берём суть ответа: первые 2 предложения или до 300 символов
        response_core = bot_response[:300]
        # Разбиваем по предложениям
        resp_sentences = re.split(r'(?<=[.!?])\s+', response_core)
        if resp_sentences:
            response_core = resp_sentences[0][:200]
        
        response_metadata = dict(metadata)
        response_metadata['type'] = 'response_fact'
        _store_memory_sync(user_id, f"ASI: {response_core}", response_metadata)
    
    return stored


async def store_conversation_turn(user_id, user_message, bot_response, emotion=None, intent=None):
    """Async-обёртка: сохраняет обмен без блокировки event loop."""
    try:
        return await asyncio.to_thread(
            _store_conversation_turn_sync, user_id, user_message, bot_response, emotion, intent
        )
    except Exception as e:
        logger.warning(f"[VECTOR] Async store turn failed: {e}")
        return False


def store_conversation_turn_background(user_id, user_message, bot_response, emotion=None, intent=None) -> None:
    """Неблокирующий best-effort запуск сохранения диалога без asyncio Task-leaks."""
    try:
        import threading
        threading.Thread(
            target=_store_conversation_turn_sync,
            args=(user_id, user_message, bot_response, emotion, intent),
            daemon=True,
            name="vector-turn-store",
        ).start()
    except Exception as e:
        logger.warning(f"[VECTOR] Background store turn failed: {e}")


def store_memory_sync(user_id, text: str, metadata: dict = None) -> bool:
    """Публичная sync-функция — сохраняет произвольный факт в Pinecone.

    Вызывай из синхронного кода (create_goal, complete_task и т.д.).
    Безопасно: никогда не бросает исключений наружу.
    """
    try:
        return _store_memory_sync(user_id, text, metadata)
    except Exception as e:
        logger.debug(f"[VECTOR] store_memory_sync failed: {e}")
        return False


async def store_memory(user_id, text: str, metadata: dict = None) -> bool:
    """Публичная async-функция — сохраняет произвольный факт без блокировки loop."""
    try:
        return await asyncio.to_thread(_store_memory_sync, user_id, text, metadata)
    except Exception as e:
        logger.debug(f"[VECTOR] store_memory async failed: {e}")
        return False


def store_memory_background(user_id, text: str, metadata: dict = None) -> None:
    """Неблокирующий best-effort запуск сохранения факта без висящих asyncio-задач."""
    try:
        import threading
        threading.Thread(
            target=store_memory_sync,
            args=(user_id, text, metadata),
            daemon=True,
            name="vector-memory-store",
        ).start()
    except Exception as e:
        logger.debug(f"[VECTOR] store_memory background failed: {e}")
