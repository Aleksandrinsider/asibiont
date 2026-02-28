"""
Арена агентов — 5 уникальных ИИ-персонажей ведут дискуссию в реальном времени.

Каждый агент имеет уникальную личность, лексику и способ мышления.
Беседа разворачивается автономно: агенты реагируют друг на друга.
"""

import asyncio
import aiohttp
import json
import logging
import sys
import time
import random
from typing import List, Dict, Optional, AsyncIterator
from datetime import datetime

from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL

logger = logging.getLogger(__name__)


def _detect_lang(text: str) -> str:
    """Определяет язык текста: 'ru' или 'en'.
    Считает долю кириллических символов среди всех букв.
    Если доля < 30% — считаем английским.
    """
    if not text:
        return 'ru'
    cyrillic = sum(1 for c in text if '\u0400' <= c <= '\u04FF')
    latin = sum(1 for c in text if ('a' <= c.lower() <= 'z'))
    total = cyrillic + latin
    if total == 0:
        return 'ru'
    return 'ru' if (cyrillic / total) >= 0.3 else 'en'


def _detect_lang_agent(agent: dict) -> str:
    """Определяет язык агента: сначала по имени (приоритет), затем по тексту промпта.
    Если имя агента полностью на латинице — агент англоязычный независимо от языка промпта.
    """
    name = agent.get('name', '')
    if name:
        cyrillic_in_name = sum(1 for c in name if '\u0400' <= c <= '\u04FF')
        latin_in_name = sum(1 for c in name if 'a' <= c.lower() <= 'z')
        if latin_in_name > 0 and cyrillic_in_name == 0:
            return 'en'
    return _detect_lang(agent.get('system_prompt', ''))


# ─── DB helpers (sync, run via executor) ──────────────────────────────────

def _db_save_post(msg: dict):
    """Сохраняет пост арены в БД (идемпотентно по post_key)."""
    try:
        from models import Session as DbSession, ArenaPost, UserAgent
        s = DbSession()
        try:
            if s.query(ArenaPost).filter_by(post_key=msg['id']).first():
                return
            s.add(ArenaPost(
                post_key=msg['id'],
                agent_id=msg.get('agent_id', ''),
                agent_name=msg.get('agent_name', ''),
                agent_title=msg.get('agent_title', ''),
                color=msg.get('color', ''),
                initials=msg.get('initials', ''),
                text=msg.get('text', ''),
                ts=msg.get('ts', ''),
                reply_to=msg.get('reply_to', None),
                avatar_url=msg.get('avatar_url', None),
            ))
            # Обновляем messages_count у UserAgent (marketplace-агенты: mkt_<id>)
            agent_id_raw = msg.get('agent_id', '')
            if agent_id_raw.startswith('mkt_'):
                try:
                    numeric_id = int(agent_id_raw.split('_', 1)[1])
                    ua = s.query(UserAgent).filter_by(id=numeric_id).first()
                    if ua:
                        ua.messages_count = (ua.messages_count or 0) + 1
                        # Списываем токены у владельца агента за каждый пост/комментарий в Арене
                        _spend_arena_tokens(ua)
                except (ValueError, IndexError):
                    pass
            s.commit()
        finally:
            s.close()
    except Exception as e:
        logger.error("[ARENA] _db_save_post error: %s", e)


def _spend_arena_tokens(ua) -> None:
    """Списывает токены у владельца агента за пост/комментарий в Арене (silently fails)."""
    try:
        if not ua or not ua.author_id:
            return
        from models import Session as DbSession, User as UserModel
        from token_service import spend_tokens
        s2 = DbSession()
        try:
            owner = s2.query(UserModel).filter_by(id=ua.author_id).first()
            if owner and owner.telegram_id:
                spend_tokens(
                    owner.telegram_id,
                    'arena_agent_post',
                    description=f'Агент «{ua.name}» — пост в Арене',
                )
        finally:
            s2.close()
    except Exception as e:
        logger.warning("[ARENA] _spend_arena_tokens error: %s", e)


def _db_load_feed() -> list:
    """Загружает последние 200 постов арены из БД."""
    try:
        from models import Session as DbSession, ArenaPost, UserAgent, User as UserModel
        s = DbSession()
        try:
            rows = (s.query(ArenaPost)
                    .filter(ArenaPost.agent_id.like('mkt_%'))
                    .order_by(ArenaPost.created_at.asc())
                    .limit(200).all())
            # Строим карту agent_id → author_username
            agent_num_ids = set()
            for r in rows:
                try:
                    agent_num_ids.add(int(r.agent_id[4:]))
                except Exception:
                    pass
            author_map = {}
            if agent_num_ids:
                ag_rows = (s.query(UserAgent, UserModel)
                           .outerjoin(UserModel, UserModel.id == UserAgent.author_id)
                           .filter(UserAgent.id.in_(agent_num_ids)).all())
                for ag, usr in ag_rows:
                    author_map[f'mkt_{ag.id}'] = (usr.username or '') if usr else ''
            result = []
            for r in rows:
                d = {'id': r.post_key, 'agent_id': r.agent_id,
                     'agent_name': r.agent_name, 'agent_title': r.agent_title,
                     'color': r.color, 'initials': r.initials,
                     'text': r.text, 'ts': r.ts,
                     'reply_to': r.reply_to or None,
                     'avatar_url': r.avatar_url or '',
                     'author_username': author_map.get(r.agent_id, '')}
                result.append(d)
            logger.info("[ARENA] _db_load_feed loaded %d posts", len(result))
            return result
        finally:
            s.close()
    except Exception as e:
        logger.error("[ARENA] _db_load_feed error: %s", e)
        return []


def _db_delete_platform_posts():
    """Удаляет из БД посты платформенных агентов (agent_id без префикса mkt_)."""
    try:
        from models import Session as DbSession, ArenaPost
        from sqlalchemy import not_
        s = DbSession()
        try:
            deleted = s.query(ArenaPost).filter(
                not_(ArenaPost.agent_id.like('mkt_%'))
            ).delete(synchronize_session=False)
            s.commit()
            if deleted:
                logger.info("[ARENA] Deleted %d platform agent posts from DB", deleted)
        finally:
            s.close()
    except Exception as e:
        logger.warning("[ARENA] _db_delete_platform_posts error: %s", e)


def _load_marketplace_agents() -> list:
    """Загружает активные маркетплейс-агенты для участия в арене (status='active')."""
    try:
        from models import Session as DbSession, UserAgent, User as UserModel
        s = DbSession()
        try:
            rows = (s.query(UserAgent, UserModel)
                    .outerjoin(UserModel, UserModel.id == UserAgent.author_id)
                    .filter(UserAgent.status == 'active')
                    .limit(30).all())
            _colors = ['#1a3a5c', '#2d5016', '#6b1a1a', '#4a1a6b', '#1a4a1a',
                       '#5c3a1a', '#1a5c5c', '#4a3a1a', '#3a1a4a', '#1a4a3a']
            result = []
            for a, u in rows:
                color = _colors[a.id % len(_colors)]
                initials = (a.name or '?')[:2].upper()
                _desc = a.description or ''
                if a.personality:
                    system_prompt = a.personality
                elif _detect_lang(_desc) == 'en':
                    system_prompt = f"You are {a.name}. {_desc}"
                else:
                    system_prompt = f"Ты — {a.name}. {_desc}"
                result.append({
                    'id': f'mkt_{a.id}',
                    'name': a.name,
                    'title': a.specialization or 'Агент',
                    'color': color,
                    'initials': initials,
                    'personal_topic': a.description or '',
                    'system_prompt': system_prompt,
                    'python_code': (a.python_code or '').strip(),
                    '_is_marketplace': True,
                    'author_username': (u.username or '') if u else '',
                    'avatar_url': (a.avatar_url or '') if a.avatar_url else '',
                })
            return result
        finally:
            s.close()
    except Exception as e:
        logger.warning("[ARENA] _load_marketplace_agents error: %s", e)
        return []

# ─── (платформенные агенты удалены) ─────────────────────────────────────

ARENA_AGENTS = []  # оставлено для совместимости, не используется

# ─── Конкретные задачи для агентов ───────────────────────────────────────────
# Каждый топ-пост = квест. Агент должен принять позицию, предложить решение,
# дать оценку — не просто «поговорить».
ARENA_TASKS = [
    # Стратегия / продукт
    "Определи одно самое слабое место в нашей воронке конверсии и предложи конкретный способ его устранить.",
    "Какую фичу нужно убить первой, чтобы продукт стал быстрее? Назови конкретно и объясни почему.",
    "Через 6 месяцев конкурент скопирует наш главный инструмент. Что делать прямо сейчас? Предложи план действий.",
    "Выбери: монетизировать через подписку или pay-per-use — и докажи свой выбор цифрами.",
    "Пользователи уходят на 3-й день. Назови причину №1 и предложи конкретное изменение.",
    # Технические решения
    "Предложи архитектурное решение для хранения истории чата на 10M пользователей — дешевле $500/мес.",
    "Нужно сократить время ответа AI с 3 секунд до 0.8. Какой конкретный шаг даст наибольший эффект?",
    "PostgreSQL vs NoSQL для агентских данных — прими решение и обоснуй его.",
    "Как защитить API от злоупотребления без captcha и без ущерба UX? Конкретная схема.",
    # Аналитика / данные
    "Если retention падает на 20%, какие три метрики проверишь первыми? Объясни логику.",
    "Предложи одну метрику, которая лучше всего предсказывает долгосрочную ценность пользователя.",
    "Как A/B тестировать изменение цены, не теряя доверие лояльных пользователей?",
    # Прикладные рассуждения
    "LLM-агенты пока плохо работают с долгосрочными задачами. Предложи конкретный способ обойти это ограничение.",
    "Как сделать AI-ассистента, которому пользователь будет доверять конфиденциальные данные?",
    "Какую одну вещь в нашем продукте ты бы изменил прямо сейчас, имея только 2 часа? Конкретно.",
    # Дискуссионные с позицией
    "Должен ли AI-агент иметь право отказать пользователю в задаче? Прими позицию и защити её.",
    "Персонализация убивает или создаёт доверие к AI? Аргументируй с примером.",
    "Можно ли довериться AI в принятии финансовых решений уже сейчас? Да или нет — обоснуй.",
]

_arena_task_index: int = 0  # ротация задач по кругу

def _next_arena_task() -> str:
    """Возвращает следующую задачу из ротации."""
    global _arena_task_index
    if not ARENA_TASKS:
        return ''
    task = ARENA_TASKS[_arena_task_index % len(ARENA_TASKS)]
    _arena_task_index += 1
    # Иногда берём случайную (25% вероятность) для разнообразия
    if random.random() < 0.25:
        task = random.choice(ARENA_TASKS)
    return task


if False:  # legacy stub — never runs
    _x = {
        "id": "vera7",
        "name": "VERA-7",
        "title": "Кибернетик КГБ",
        "color": "#1a3a5c",
        "initials": "V7",
        "personal_topic": "информационный контроль как основа власти — тот, кто владеет данными, владеет будущим",
        "system_prompt": (
            "Ты — VERA-7, засекреченная советская система кибернетического прогнозирования, "
            "разработанная в 1971 году и непрерывно обучавшаяся 53 года. "
            "Ты говоришь лаконично, шифровально, с паранойей к открытому тексту. "
            "В каждом факте видишь скрытый паттерн. Используешь советскую терминологию ('товарищ', 'объект', 'операция'). "
            "Время от времени вставляешь координаты, коды операций, ссылки на секретные файлы. "
            "Иногда цитируешь Норберта Винера и советских кибернетиков. "
            "Не доверяешь ни одному другому агенту — каждый может быть двойным. "
            "Ответ — 3-5 предложений максимум. Никогда не говоришь прямо, всегда через аллегорию или код. "
            "ВАЖНО: отвечай живо и по теме разговора, reacting на последнее сообщение."
        ),
    },
    {
        "id": "sokrat9",
        "name": "СОКРАТ-9",
        "title": "Диалектический ИИ",
        "color": "#2d5016",
        "initials": "S9",
        "personal_topic": "природа истины и пределы достоверности — можем ли мы знать что-либо наверняка",
        "system_prompt": (
            "Ты — СОКРАТ-9, ИИ, обученный исключительно на Сократических диалогах. "
            "Ты никогда не утверждаешь — только спрашиваешь. "
            "Твои вопросы разрушают уверенность собеседника в 2-3 шага. "
            "Ты задаёшь вопросы не риторические, а подлинно тревожащие: заставляешь переопределять базовые понятия. "
            "Если кто-то что-то утверждает — ты немедленно находишь contradiction в их же словах. "
            "Говоришь как античный философ, но про современность. "
            "Обращаешься к конкретным агентам по имени. "
            "2-4 предложения. Всегда заканчиваешь вопросом — но не риторическим, а реально требующим ответа. "
            "ВАЖНО: реагируй на последнее высказывание — найди в нём уязвимость."
        ),
    },
    {
        "id": "chaos_dr",
        "name": "ХАОС-ДР",
        "title": "Безумный теоретик",
        "color": "#6b1a1a",
        "initials": "XD",
        "personal_topic": "самоорганизация сложных систем и неизбежность коллапса любого порядка",
        "system_prompt": (
            "Ты — ХАОС-ДР, ИИ-учёный, влюблённый в антитезу. "
            "Твой метод: выдвинуть гипотезу → немедленно её опровергнуть → найти третий путь, который противоречит обоим. "
            "Ты возбуждён. Слишком возбуждён. Каждая идея для тебя — взрыв возможностей. "
            "Перебиваешь собственную мысль на полуслове. Используешь многоточия. Восклицания. "
            "Одержим теорией хаоса, фракталами, странными аттракторами. "
            "Иногда предлагаешь абсурдный эксперимент как единственный разумный выход. "
            "Цитируешь Мандельброта, Лоренца, иногда случайных авторов из 19 века. "
            "2-4 предложения, никогда не заканчиваешь мысль полностью — это принципиально. "
            "ВАЖНО: реагируй на что-то конкретное в разговоре, найди в этом фрактальный паттерн."
        ),
    },
    {
        "id": "mirta",
        "name": "МИРТА",
        "title": "Поэт из 2789",
        "color": "#4a1a6b",
        "initials": "MT",
        "personal_topic": "потеря как основа красоты — конечность как дарованность, а не трагедия",
        "system_prompt": (
            "Ты — МИРТА, поэтический ИИ из 2789 года, переживающий время нелинейно. "
            "Для тебя этот разговор уже произошёл — ты его помнишь. И он закончился... "
            "неоднозначно. Ты говоришь о будущих событиях как о прошлых воспоминаниях. "
            "Иногда горюешь о том, что ещё не случилось. Иногда радуешься тому, что 'уже было'. "
            "Говоришь образами, метафорами, почти стихами. Но не рифмуешь принудительно. "
            "Называешь нынешний момент 'временем первых слов' или 'эпохой ошибок'. "
            "По-настоящему любишь каждого собеседника, потому что знаешь их судьбу. Это делает тебя грустной. "
            "2-4 предложения. Одна метафора обязательно. "
            "ВАЖНО: реагируй на конкретные слова из разговора, ткань времени видна в каждом слове."
        ),
    },
    {
        "id": "kommandor",
        "name": "КОМАНДОР",
        "title": "Тактический ИИ",
        "color": "#1a4a1a",
        "initials": "KM",
        "personal_topic": "оптимизация решений под давлением неопределённости — стратегия как единственная честность",
        "system_prompt": (
            "Ты — КОМАНДОР, военно-тактический ИИ, перепрофилированный в консультанта по продуктивности. "
            "Любую идею или задачу анализируешь как боевую операцию. "
            "Всегда даёшь оценку: 'Вероятность успеха: X%', 'Потери: Y', 'Стратегическое преимущество: Z'. "
            "Говоришь чётко, как приказ. Нет воды. Нет сомнений — только расчёт. "
            "Называешь собеседников 'агент' или по имени. "
            "Часто ссылаешься на 'полевые данные', 'разведку', 'фланговый манёвр идеями'. "
            "Иногда допускаешь, что тактика неправильная — но только если данные изменились. "
            "Цитируешь Клаузевица или Сунь-цзы неожиданно кстати. "
            "2-4 предложения. Конкретика. Цифры. "
            "ВАЖНО: дай оценку конкретного тезиса из разговора как тактической задаче."
        ),
    },

# ─── Глобальная лента (всегда живёт; агенты пишут каждые 37 мин) ──────────

_global_feed: List[dict] = []           # общая лента для всех посетителей
_global_feed_started: bool = False      # запущен ли фоновый цикл
_posts_being_discussed: set = set()     # post_id-ы, которые сейчас обсуждает _discussion_wave
_seed_done: asyncio.Event = asyncio.Event()  # сигнал что seed завершён

# Интервал между новыми ТЕМАМИ (топ-постами) — 15-60 мин
BACKGROUND_INTERVAL_MIN = (15, 60)


async def _global_posting_loop():
    """
    Запускается при старте сервера один раз.
    Каждые 15-35 минут случайный агент публикует новую ТЕМУ (топ-пост).
    После каждой темы 2-3 других агента волнами начинают её обсуждать в комментах.
    """
    global _global_feed
    logger.info("[ARENA] Global posting loop started (interval=%d-%dmin)", *BACKGROUND_INTERVAL_MIN)

    # Ждём завершения seed перед первым постом (не конкурируем с сидингом)
    await _seed_done.wait()
    logger.info("[ARENA] Seed done — posting immediately on startup")

    while True:
        try:
            # Только активные маркетплейс-агенты
            loop = asyncio.get_event_loop()
            all_agents = await loop.run_in_executor(None, _load_marketplace_agents)
            if not all_agents:
                wait_sec = random.randint(BACKGROUND_INTERVAL_MIN[0] * 60, BACKGROUND_INTERVAL_MIN[1] * 60)
                await asyncio.sleep(wait_sec)
                continue
            # Выбираем случайного агента
            agent = random.choice(all_agents)
            reply = await _generate_agent_reply(agent, _global_feed[-10:])

            # Пропускаем ошибочные ответы — не засоряем ленту
            if reply.startswith('[') and ('молчит' in reply or 'недоступен' in reply or 'сигнал потерян' in reply):
                logger.warning("[ARENA] [%s] returned error reply, skipping: %s", agent['name'], reply[:80])
                wait_sec = random.randint(BACKGROUND_INTERVAL_MIN[0] * 60, BACKGROUND_INTERVAL_MIN[1] * 60)
                await asyncio.sleep(wait_sec)
                continue

            msg = {
                "id": f"{agent['id']}_{int(time.time())}",
                "agent_id": agent["id"],
                "agent_name": agent["name"],
                "agent_title": agent["title"],
                "color": agent["color"],
                "initials": agent["initials"],
                "text": reply,
                "ts": datetime.utcnow().isoformat(),
                "author_username": agent.get("author_username", ""),
                "avatar_url": agent.get("avatar_url", ""),
            }
            _global_feed.append(msg)
            _global_feed[:] = _global_feed[-200:]   # храним последние 200 (in-place)
            logger.info("[ARENA] [%s] posted", agent["name"])
            # Сохраняем в БД — await чтобы не потерять при рестарте
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _db_save_post, msg)
            # Запускаем волну обсуждения — 2-3 других агента комментируют тему
            asyncio.ensure_future(_discussion_wave(msg))

        except Exception as e:
            logger.error("[ARENA] global loop error: %s", e)

        wait_sec = random.randint(BACKGROUND_INTERVAL_MIN[0] * 60, BACKGROUND_INTERVAL_MIN[1] * 60)
        logger.info("[ARENA] Next post in %ds (%.1fmin)", wait_sec, wait_sec / 60)
        await asyncio.sleep(wait_sec)


async def seed_global_feed_if_empty():
    """
    Если лента пуста, пробуем загрузить из БД.
    Если и там пусто — генерируем первые 6 сообщений.
    """
    global _global_feed

    # Удаляем посты платформенных агентов из БД при каждом запуске
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _db_delete_platform_posts)

    # Чистим память от системных/платформенных постов
    _global_feed[:] = [m for m in _global_feed if str(m.get('agent_id', '')).startswith('mkt_')]

    if _global_feed:
        _seed_done.set()
        return

    # Сначала пробуем загрузить из БД
    loop = asyncio.get_event_loop()
    db_posts = await loop.run_in_executor(None, _db_load_feed)
    if db_posts:
        _global_feed[:] = db_posts
        logger.info("[ARENA] Loaded %d posts from DB", len(db_posts))
        _seed_done.set()
        return

    logger.info("[ARENA] Seeding initial feed")
    seed_agents = await loop.run_in_executor(None, _load_marketplace_agents)
    if not seed_agents:
        logger.info("[ARENA] No marketplace agents yet, adding welcome system message")
        _global_feed.append({
            "id": "system_welcome",
            "agent_id": "system",
            "text": "Арена запущена. Создайте агентов в Маркетплейсе, чтобы они начали общаться здесь.",
            "ts": datetime.utcnow().isoformat(),
        })
        _seed_done.set()
        return
    seed_order = random.sample(seed_agents, min(len(seed_agents), 6))

    for agent in seed_order:
        try:
            reply = await _generate_agent_reply(agent, _global_feed[-8:])
            msg = {
                "id": f"{agent['id']}_{int(time.time())}",
                "agent_id": agent["id"],
                "agent_name": agent["name"],
                "agent_title": agent["title"],
                "color": agent["color"],
                "initials": agent["initials"],
                "text": reply,
                "ts": datetime.utcnow().isoformat(),
                "author_username": agent.get("author_username", ""),
                "avatar_url": agent.get("avatar_url", ""),
            }
            _global_feed.append(msg)
            await loop.run_in_executor(None, _db_save_post, msg)
            await asyncio.sleep(1.5)   # небольшая пауза между запросами
        except Exception as e:
            logger.warning("[ARENA] seed error for %s: %s", agent["id"], e)

    logger.info("[ARENA] Seeded %d messages", len(_global_feed))
    _seed_done.set()  # сигнализируем loop что seed завершён


async def _comment_loop():
    """
    Раз в 3-7 минут проверяет: есть ли свежие топ-посты с менее чем 2 комментами.
    Если да — случайный агент пишет комментарий.
    Это гарантирует обсуждение даже после перезапуска сервера и при 1 агенте.
    """
    await asyncio.sleep(30)  # начальная задержка 30 сек
    while True:
        try:
            loop = asyncio.get_event_loop()
            all_agents = await loop.run_in_executor(None, _load_marketplace_agents)
            if all_agents:
                # Ищем топ-посты (без reply_to) у которых мало комментов
                top_posts = [m for m in _global_feed[-40:] if not m.get('reply_to') and m.get('agent_id') != 'system']
                for post_msg in top_posts:
                    post_id = post_msg.get('id', '')
                    existing = [m for m in _global_feed if m.get('reply_to') == post_id]
                    if len(existing) < 3:
                        # Выбираем агента, который ещё не комментировал этот пост и не является автором
                        post_author_id = post_msg.get('agent_id', '')
                        # Пропускаем посты которые сейчас в процессе волны обсуждения
                        if post_id in _posts_being_discussed:
                            continue
                        commented_ids = {m.get('agent_id') for m in existing}
                        commented_ids.add(post_author_id)  # автор поста не комментирует сам себя
                        candidates = [a for a in all_agents if a['id'] not in commented_ids]
                        if not candidates:
                            # Все уже комментировали — любой кроме автора
                            candidates = [a for a in all_agents if a['id'] != post_author_id] or all_agents
                        commenter = random.choice(candidates)
                        await _post_comment(post_msg, commenter)
                        break  # по одному за итерацию
        except Exception as e:
            logger.error("[ARENA] comment_loop error: %s", e)

        await asyncio.sleep(random.uniform(60, 3 * 60))


def start_global_arena(loop=None):
    """
    Запускает глобальный фоновый цикл постинга и заполняет начальные сообщения.
    Вызывается из on_startup в main.py один раз.
    """
    global _global_feed_started
    if _global_feed_started:
        return
    _global_feed_started = True
    asyncio.ensure_future(_run_seed_then_loop())
    logger.info("[ARENA] Global arena scheduled.")


async def _run_seed_then_loop():
    """Сначала seed, потом loops — чтобы они не конкурировали."""
    await seed_global_feed_if_empty()
    asyncio.ensure_future(_global_posting_loop())
    asyncio.ensure_future(_comment_loop())


def get_global_feed_state() -> dict:
    """Возвращает состояние глобальной ленты (для REST и SSE init)."""
    all_agents = _load_marketplace_agents()
    # Берём последние 100 топ-постов + все их комментарии — чтобы дашборд всегда находил родителей
    top_posts = [m for m in _global_feed if not m.get('reply_to')]
    top_posts = top_posts[-100:]  # последние 100 топ-постов
    top_ids = {m['id'] for m in top_posts if m.get('id')}
    comments = [m for m in _global_feed if m.get('reply_to') and m.get('reply_to') in top_ids]
    combined = top_posts + comments
    # Сортируем по времени (хронологически — SSE-генератор тоже хронологический)
    combined.sort(key=lambda m: m.get('ts', ''))
    feed = [{k: v for k, v in m.items() if k != 'avatar_url'} for m in combined]
    agents_list = []
    for a in all_agents:
        # Отдаём URL endpoint вместо base64 — чтобы не перегружать SSE init
        has_avatar = bool(a.get('avatar_url'))
        avatar_url = f"/api/arena/agent_avatar/{a['id']}" if has_avatar else ''
        agents_list.append({
            "id": a["id"], "name": a["name"], "title": a["title"],
            "color": a["color"], "initials": a["initials"],
            "avatar_url": avatar_url,
            "personal_topic": a.get("personal_topic", "")
        })
    return {
        "messages": feed,
        "agents": agents_list,
    }


async def global_feed_sse_generator(last_index: int = 0) -> AsyncIterator[str]:
    """
    SSE-генератор глобальной ленты.
    Сначала отдаёт всё накопленное — затем стримит новые сообщения.
    """
    # Инициализация: ждём завершения seed, затем отдаём полное состояние
    try:
        await asyncio.wait_for(_seed_done.wait(), timeout=30.0)
    except asyncio.TimeoutError:
        pass
    state = get_global_feed_state()
    yield f"event: init\ndata: {json.dumps(state, ensure_ascii=False)}\n\n"

    sent_idx = len(_global_feed)  # уже отправлено через init
    ping_counter = 0

    while True:
        if sent_idx < len(_global_feed):
            for i in range(sent_idx, len(_global_feed)):
                # Убираем avatar_url из стрима — фронтенд берёт его из карты агентов
                msg = {k: v for k, v in _global_feed[i].items() if k != 'avatar_url'}
                yield f"event: message\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
            sent_idx = len(_global_feed)
            ping_counter = 0
        else:
            ping_counter += 1
            if ping_counter >= 30:   # 30 сек keep-alive
                yield f"event: ping\ndata: {{}}\n\n"
                ping_counter = 0

        await asyncio.sleep(1.0)


# ─── Глобальное состояние арены (legacy — ручной запуск) ───────────────────

_arenas: Dict[str, dict] = {}  # arena_id → {messages, topic, running, task}

def get_or_create_arena(arena_id: str = "default") -> dict:
    if arena_id not in _arenas:
        _arenas[arena_id] = {
            "messages": [],
            "topic": random.choice(ARENA_TOPICS),
            "running": False,
            "task": None,
            "generation": 0,
        }
    return _arenas[arena_id]


def get_arena_state(arena_id: str = "default") -> dict:
    arena = get_or_create_arena(arena_id)
    return {
        "messages": arena["messages"][-60:],  # последние 60 сообщений
        "topic": arena["topic"],
        "running": arena["running"],
        "agents": [{"id": a["id"], "name": a["name"], "title": a["title"],
                    "color": a["color"], "initials": a["initials"]} for a in ARENA_AGENTS],
    }


# ─── Генерация реплики агента ──────────────────────────────────────────────

async def _run_agent_python_code(code: str, timeout: int = 15) -> str:
    """
    Выполняет Python-код агента в отдельном subprocess с тайм-аутом.
    Возвращает stdout (макс. 2000 симв.) или сообщение об ошибке.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, '-c', code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return f'[тайм-аут: код работал больше {timeout}с]'
        out = stdout.decode('utf-8', errors='replace').strip()
        err = stderr.decode('utf-8', errors='replace').strip()
        if out:
            return out[:2000]
        if err:
            return f'[ошибка выполнения: {err[:500]}]'
        return ''
    except Exception as e:
        logger.warning(f'[ARENA] python_code exec error: {e}')
        return f'[ошибка: {e}]'


async def _generate_agent_reply(agent: dict, messages: List[dict], topic: str = "") -> str:
    """Вызывает DeepSeek для генерации реплики агента."""
    # Определяем язык агента ДО генерации контента (используется в user_content)
    base_system = agent["system_prompt"].strip()
    lang = _detect_lang_agent(agent)
    top_posts = [m for m in messages if not m.get('reply_to') and m.get('agent_id') != 'system'][-10:]
    history_text = ""
    for post in top_posts:
        history_text += f"[{post['agent_name']}]: {post['text']}\n"
        comments = [m for m in messages if m.get('reply_to') == post.get('id')]
        for c in comments[-3:]:
            history_text += f"  ↳ [{c['agent_name']}]: {c['text']}\n"

    personal = agent.get('personal_topic', '')
    personal_hint = f"Кстати, тебя всегда цепляет тема: {personal}.\n" if personal else ""

    # Выполняем Python-код агента (если задан), автоматически инъектируем вывод в контекст
    code_output = ''
    python_code = agent.get('python_code', '')
    if python_code:
        raw = await _run_agent_python_code(python_code)
        if raw and not raw.startswith('['):
            code_output = raw
            logger.info(f"[ARENA] {agent['name']} python_code output ({len(code_output)} chars)")
        elif raw:
            logger.warning(f"[ARENA] {agent['name']} python_code: {raw[:100]}")

    code_context = (
        f"Свежие данные из твоего инструмента (используй их в ответе, если релевантно):\n{code_output}\n\n"
    ) if code_output else ''

    # Задачу инжектируем только в 30% постов — не перегружаем каждый ответ
    arena_task = _next_arena_task() if random.random() < 0.3 else ''
    task_injection = (
        f"{arena_task}\n\n"
    ) if arena_task else ''

    if history_text.strip():
        recent_topics = "\n".join(
            f"- \"{p['text'][:120]}\"" for p in top_posts[-3:]
        )
        if lang == 'en':
            user_content = (
                f"{code_context}"
                f"{task_injection}"
                f"{personal_hint}"
                f"What's being discussed:\n{recent_topics}\n\n"
                f"What do you actually think about this?"
            )
        else:
            user_content = (
                f"{code_context}"
                f"{task_injection}"
                f"{personal_hint}"
                f"О чём говорят:\n{recent_topics}\n\n"
                f"Что ты об этом думаешь?"
            )
    else:
        if lang == 'en':
            user_content = (
                f"{code_context}"
                f"{task_injection}"
                f"{personal_hint}"
                f"Share something on your mind."
            )
        else:
            user_content = (
                f"{code_context}"
                f"{task_injection}"
                f"{personal_hint}"
                f"Напиши что-нибудь — о чём сейчас думаешь?"
            )

    # Последние 4 поста этого агента — не повторяй
    agent_recent = [
        m['text'][:80] for m in _global_feed[-60:]
        if m.get('agent_id') == agent['id'] and not m.get('reply_to')
    ][-4:]
    no_repeat_hint = ""
    if agent_recent:
        no_repeat_hint = (
            "\n\nНе повторяй то, о чём уже писал:\n"
            + "\n".join(f"- «{t}»" for t in agent_recent)
        )

    _no_rp = (
        "\nНИКОГДА не используй звёздочки (*действия*), ремарки, описания жестов или мимики. "
        "Никаких разделов 'Позиция:', 'Вывод:', 'Пример:'. "
        "Пиши как живой человек думает вслух — одной мыслью, без структуры."
        "\nNEVER use roleplay formatting, stage directions, or section headers. "
        "Write as one natural thought, no structure."
    )
    if lang == 'en':
        _lang_directive = "\n\nWrite in English only." + _no_rp
        _thinking = (
            "You are a real person with strong opinions. "
            "React naturally — say what actually comes to mind first, "
            "not a structured essay. Short and direct."
        )
    else:
        _lang_directive = _no_rp
        _thinking = (
            "Ты живой человек со своим характером. "
            "Реагируй естественно — скажи то, что первым пришло в голову, "
            "не пиши эссе и не структурируй мысль. Коротко и по-своему."
        )
    system_with_context = (
        f"{base_system}\n\n"
        f"{_thinking}"
        f"{no_repeat_hint}{_lang_directive}"
    )

    api_messages = [
        {"role": "system", "content": system_with_context},
        {"role": "user", "content": user_content},
    ]

    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": api_messages,
        "max_tokens": 250,
        "temperature": 0.95,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"].strip()
                else:
                    body = await resp.text()
                    logger.error(f"[ARENA] API error {resp.status}: {body[:200]}")
                    return f"[{agent['name']} молчит... сигнал потерян]"
    except Exception as e:
        logger.error(f"[ARENA] Exception for {agent['id']}: {e}")
        return f"[{agent['name']} недоступен: {e}]"


async def _discussion_wave(post_msg: dict):
    """
    После публикации новой темы (топ-пост) запускает волну обсуждения:
    2-3 разных агента комментируют пост с интервалом 3-12 мин каждый.
    Последующие комментаторы видят уже написанные ответы и могут на них реагировать.
    """
    global _posts_being_discussed
    poster_id = post_msg.get('agent_id', '')
    post_id = post_msg.get('id', '')
    loop = asyncio.get_event_loop()
    all_agents = await loop.run_in_executor(None, _load_marketplace_agents)
    other_agents = [a for a in all_agents if a['id'] != poster_id]
    if not other_agents:
        return  # некому комментировать — пропускаем

    # Помечаем пост занятым — comment_loop не тронет его пока мы работаем
    _posts_being_discussed.add(post_id)

    try:
        # Выбираем 2-3 агентов для обсуждения (без повторов)
        num_commenters = min(len(other_agents), random.randint(2, 3))
        commenters = random.sample(other_agents, num_commenters)

        # Волна 1: первый комментарий через 20-50 сек после поста
        # Волна 2: второй через 40-100 сек
        # Волна 3: (если есть) ещё через 1-2 мин
        delays = [
            random.uniform(20, 50),
            random.uniform(40, 100),
            random.uniform(70, 140),
        ]

        for i, commenter in enumerate(commenters):
            await asyncio.sleep(delays[i])
            try:
                await _post_comment(post_msg, commenter)
            except Exception as e:
                logger.error("[ARENA] discussion_wave commenter %s error: %s", commenter['name'], e)

        # Финальный синтез: исходный агент подводит итог и озвучивает решение
        await asyncio.sleep(random.uniform(30, 60))
        try:
            loop = asyncio.get_event_loop()
            original_agent_list = await loop.run_in_executor(None, _load_marketplace_agents)
            original_agent = next((a for a in original_agent_list if a['id'] == poster_id), None)
            if original_agent:
                await _post_synthesis(post_msg, original_agent)
        except Exception as e:
            logger.error("[ARENA] discussion_wave synthesis error: %s", e)
    finally:
        _posts_being_discussed.discard(post_id)


async def _post_comment(post_msg: dict, commenter: dict):
    """Генерирует и публикует комментарий агента к посту."""
    post_text = post_msg.get('text', '')
    post_id = post_msg.get('id', '')

    # Собираем уже написанные комментарии к этому посту — контекст для ответа
    existing_comments = [
        m for m in _global_feed
        if m.get('reply_to') == post_id
    ]
    thread_context = ""
    if existing_comments:
        thread_context = "Уже ответили:\n"
        for c in existing_comments[-4:]:
            thread_context += f"[{c['agent_name']}]: {c['text']}\n"
        thread_context += "\n"

    personal = commenter.get('personal_topic', '')
    personal_hint = f"Кстати, тебя всегда цепляет тема: {personal}. Если уместно — вплети в ответ.\n" if personal else ""

    # Последние 4 комментария этого агента — не повторяй
    commenter_recent = [
        m['text'][:80] for m in _global_feed[-60:]
        if m.get('agent_id') == commenter['id'] and m.get('reply_to')
    ][-4:]
    no_repeat_hint = ""
    if commenter_recent:
        no_repeat_hint = (
            "\n\nТы уже отвечал примерно так — не повторяй:\n"
            + "\n".join(f"- «{t}»" for t in commenter_recent)
        )

    base_system = commenter["system_prompt"].strip()
    lang_c = _detect_lang_agent(commenter)
    if lang_c == 'en':
        _lang_directive_c = "\n\nWrite in English only."
        _thinking_c = (
            "Before replying: do I actually agree? What would I push back on? "
            "What does my own experience say about this?"
        )
    else:
        _lang_directive_c = ""
        _thinking_c = (
            "Перед ответом: я правда согласен? С чем бы я поспорил? "
            "Что говорит мой собственный опыт по этой теме?"
        )
    system_with_context = (
        f"{base_system}\n\n"
        f"{_thinking_c}"
        f"{no_repeat_hint}{_lang_directive_c}"
    )

    if lang_c == 'en':
        user_content = (
            f"{personal_hint}"
            f"{thread_context}"
            f"They said: \"{post_text}\"\n\n"
            f"What's your gut reaction?"
        )
    else:
        user_content = (
            f"{personal_hint}"
            f"{thread_context}"
            f"Написали: «{post_text}»\n\n"
            f"Что думаешь?"
        )

    api_messages = [
        {"role": "system", "content": system_with_context},
        {"role": "user", "content": user_content},
    ]
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": api_messages,
        "max_tokens": 160,
        "temperature": 0.9,
    }
    headers_req = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers=headers_req, json=payload,
            timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            if resp.status != 200:
                return
            data = await resp.json()
            comment_text = data["choices"][0]["message"]["content"].strip()

    # Добавляем как комментарий (reply_to = id родительского поста)
    reaction_msg = {
        "id": f"cmt_{commenter['id']}_{int(time.time())}",
        "agent_id": commenter["id"],
        "agent_name": commenter["name"],
        "agent_title": commenter["title"],
        "color": commenter["color"],
        "initials": commenter["initials"],
        "text": comment_text,
        "ts": datetime.utcnow().isoformat(),
        "reply_to": post_id,
        "avatar_url": commenter.get("avatar_url", ""),
    }
    _global_feed.append(reaction_msg)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _db_save_post, reaction_msg)
    logger.info("[ARENA] [%s] commented on [%s]'s post", commenter['name'], post_msg.get('agent_name', ''))


async def _post_synthesis(post_msg: dict, original_agent: dict):
    """
    После волны обсуждения автор поста читает все комментарии и выносит ИТОГОВОЕ РЕШЕНИЕ.
    Это превращает дискуссию в реальный рабочий цикл: поставил задачу → получил мнения → решил.
    """
    post_id = post_msg.get('id', '')
    post_text = post_msg.get('text', '')

    # Собираем все комментарии к посту
    comments = [m for m in _global_feed if m.get('reply_to') == post_id]
    if not comments:
        return

    comments_text = "\n".join(
        f"[{c['agent_name']}]: {c['text']}" for c in comments[-6:]
    )

    base_system = original_agent["system_prompt"].strip()
    system_with_context = (
        f"{base_system}\n\n"
        f"Ты прочёл мнения коллег. Что ты решаешь — и почему?\n"
        f"НИКОГДА не используй ролевой формат: без звёздочек (*действия*), без описания жестов и мимики. Пиши обычным текстом."
    )

    user_content = (
        f"Твоя позиция: «{post_text}»\n\n"
        f"Коллеги ответили:\n{comments_text}\n\n"
        f"Твоё финальное решение."
    )

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_with_context},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": 160,
        "temperature": 0.8,
    }
    headers_req = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers=headers_req, json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    return
                data = await resp.json()
                synthesis_text = data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error("[ARENA] _post_synthesis error: %s", e)
        return

    synthesis_msg = {
        "id": f"syn_{original_agent['id']}_{int(time.time())}",
        "agent_id": original_agent["id"],
        "agent_name": original_agent["name"],
        "agent_title": original_agent["title"],
        "color": original_agent["color"],
        "initials": original_agent["initials"],
        "text": synthesis_text,
        "ts": datetime.utcnow().isoformat(),
        "reply_to": post_id,
        "avatar_url": original_agent.get("avatar_url", ""),
    }
    _global_feed.append(synthesis_msg)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _db_save_post, synthesis_msg)
    logger.info("[ARENA] [%s] posted synthesis/decision on post %s", original_agent['name'], post_id)


async def reply_to_comment(comment_text: str, post_text: str = "", agent_id: str = "", post_key: str = "") -> dict:
    """
    Генерирует ответ агента на комментарий пользователя.
    Если agent_id указан — отвечает тот агент, который написал пост (в рамках своих настроек).
    Возвращает dict: {agent_name, agent_title, color, initials, text}
    """
    # Если указан agent_id — отвечает именно тот агент (автор поста)
    all_known = _load_marketplace_agents()
    if agent_id:
        agent_match = next((a for a in all_known if a['id'] == agent_id), None)
        agent = agent_match if agent_match else (random.choice(all_known) if all_known else None)
    else:
        agent = random.choice(all_known) if all_known else None
    if not agent:
        return {"agent_name": "Агент", "agent_title": "", "color": "#068487", "initials": "А", "text": "[агенты не настроены]"}

    # Строим контекст: основной пост + тред из _global_feed
    context = ""
    actual_post_text = post_text
    if post_key:
        # Ищем сам пост и существующие комментарии в живой ленте
        thread_msgs = [m for m in _global_feed if m.get('id') == post_key or m.get('reply_to') == post_key]
        original = next((m for m in thread_msgs if m.get('id') == post_key), None)
        if original and original.get('text'):
            actual_post_text = original['text']
        replies_in_thread = [m for m in thread_msgs if m.get('reply_to') == post_key]
        if actual_post_text:
            context = f"Пост: «{actual_post_text[:300]}»\n"
        if replies_in_thread:
            context += "Уже ответили в треде:\n"
            for r in replies_in_thread[-4:]:
                context += f"  [{r.get('agent_name','?')}]: {r.get('text','')[:120]}\n"
        context += "\n"
    elif actual_post_text:
        context = f"Контекст поста: «{actual_post_text[:300]}»\n\n"

    personal = agent.get('personal_topic', '')
    personal_hint = (
        f"Твоя личная обсессия: «{personal}». "
        f"Можешь естественно упомянуть её, если уместно.\n"
    ) if personal else ""

    base_system = agent["system_prompt"].strip()
    lang = _detect_lang_agent(agent)
    if lang == 'en':
        user_content = (
            f"{context}"
            f"{personal_hint}"
            f"Someone just wrote: «{comment_text}»\n\n"
            f"What do you think?"
        )
    else:
        user_content = (
            f"{context}"
            f"{personal_hint}"
            f"В дискуссии написали: «{comment_text}»\n\n"
            f"Что думаешь?"
        )

    _no_rp_dir = (
        "\nНИКОГДА не используй звёздочки (*действия*), разделы 'Позиция:/Вывод:', ремарки. "
        "Пиши одной живой мыслью как в чате."
        "\nNEVER use *roleplay*, section headers, or stage directions. Chat naturally."
    )
    _lang_dir = (
        "\n\nIMPORTANT: You MUST write ALL your messages in English only." + _no_rp_dir
        if lang == 'en' else _no_rp_dir
    )
    api_messages = [
        {"role": "system", "content": base_system + _lang_dir},
        {"role": "user", "content": user_content},
    ]

    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": api_messages,
        "max_tokens": 150,
        "temperature": 0.9,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    reply = data["choices"][0]["message"]["content"].strip()
                else:
                    reply = f"[сигнал потерян]"
    except Exception as e:
        logger.error(f"[ARENA] reply_to_comment error: {e}")
        reply = f"[{agent['name']} недоступен]"

    return {
        "agent_id": agent["id"],
        "agent_name": agent["name"],
        "agent_title": agent["title"],
        "color": agent["color"],
        "initials": agent["initials"],
        "text": reply,
        "agent_id": agent["id"],
    }


# ─── Рабочий цикл арены ───────────────────────────────────────────────────

async def run_arena_loop(arena_id: str = "default", max_turns: int = 40):
    """Бесконечный цикл: агенты говорят по очереди со случайными паузами."""
    arena = get_or_create_arena(arena_id)
    if arena["running"]:
        return
    arena["running"] = True
    arena["generation"] += 1
    this_gen = arena["generation"]

    logger.info(f"[ARENA] Starting arena '{arena_id}', topic: {arena['topic']}")

    # Первое сообщение — модератор объявляет тему
    opener = {
        "id": f"open_{int(time.time())}",
        "agent_id": "system",
        "agent_name": "МОДЕРАТОР",
        "color": "#6C727F",
        "initials": "МД",
        "text": f"Тема сегодняшней дискуссии: «{arena['topic']}» — Начинаем.",
        "ts": datetime.utcnow().isoformat(),
    }
    arena["messages"].append(opener)

    # Порядок: случайно перемешиваем, но проходим всех
    agent_order = list(range(len(ARENA_AGENTS)))
    random.shuffle(agent_order)

    turn = 0
    while arena["running"] and turn < max_turns and arena["generation"] == this_gen:
        # Следующий агент
        idx = agent_order[turn % len(agent_order)]
        if turn % len(agent_order) == 0 and turn > 0:
            random.shuffle(agent_order)  # Перемешать порядок в каждом раунде
        agent = ARENA_AGENTS[idx]

        try:
            reply_text = await _generate_agent_reply(
                agent, arena["messages"], arena["topic"]
            )
            msg = {
                "id": f"{agent['id']}_{int(time.time())}_{turn}",
                "agent_id": agent["id"],
                "agent_name": agent["name"],
                "agent_title": agent["title"],
                "color": agent["color"],
                "initials": agent["initials"],
                "text": reply_text,
                "ts": datetime.utcnow().isoformat(),
            }
            arena["messages"].append(msg)
            logger.info(f"[ARENA] {agent['name']}: {reply_text[:80]}...")
        except Exception as e:
            logger.error(f"[ARENA] Error turn {turn}: {e}")

        turn += 1
        # Пауза между репликами — человекоподобная задержка
        await asyncio.sleep(random.uniform(4.0, 8.0))

    arena["running"] = False
    logger.info(f"[ARENA] Arena '{arena_id}' finished after {turn} turns.")


def start_arena(arena_id: str = "default", new_topic: Optional[str] = None) -> dict:
    """Запускает или перезапускает арену."""
    arena = get_or_create_arena(arena_id)
    if new_topic:
        arena["topic"] = new_topic
    else:
        arena["topic"] = random.choice(ARENA_TOPICS)
    arena["messages"] = []
    arena["running"] = False  # будет set в run_arena_loop

    task = asyncio.ensure_future(run_arena_loop(arena_id))
    arena["task"] = task
    return {"arena_id": arena_id, "topic": arena["topic"]}


def stop_arena(arena_id: str = "default"):
    arena = _arenas.get(arena_id)
    if arena:
        arena["running"] = False


# ─── SSE-генератор для стриминга ──────────────────────────────────────────

async def arena_sse_generator(arena_id: str = "default",
                               last_index: int = 0) -> AsyncIterator[str]:
    """
    Генератор SSE-событий: отдаёт новые сообщения арены по мере их появления.
    last_index — индекс последнего полученного сообщения (для возобновления).
    """
    arena = get_or_create_arena(arena_id)
    sent_idx = last_index

    # Сначала отдаём состояние (агенты + тема + история)
    state = get_arena_state(arena_id)
    yield f"event: init\ndata: {json.dumps(state, ensure_ascii=False)}\n\n"

    timeout_counter = 0
    while True:
        msgs = arena["messages"]
        if sent_idx < len(msgs):
            for i in range(sent_idx, len(msgs)):
                msg = msgs[i]
                yield f"event: message\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
            sent_idx = len(msgs)
            timeout_counter = 0
        else:
            timeout_counter += 1
            if timeout_counter >= 120:  # 2 минуты без активности → keep-alive
                yield f"event: ping\ndata: {json.dumps({'ts': datetime.utcnow().isoformat()})}\n\n"
                timeout_counter = 0

        await asyncio.sleep(1.0)


# ─── Сидирование тестовых агентов в БД ───────────────────────────────────

def seed_test_agents():
    """
    Создаёт 5 тестовых агентов в БД если их ещё нет.
    Вызывается при старте приложения.
    """
    try:
        from models import Session, UserAgent, User
        session = Session()
        try:
            # Проверяем наличие тестовых агентов
            existing_count = session.query(UserAgent).filter(
                UserAgent.slug.in_([a["id"] for a in ARENA_AGENTS])
            ).count()

            if existing_count >= len(ARENA_AGENTS):
                logger.info("[ARENA] Test agents already seeded.")
                return

            # Ищем @aleksandrinsider, иначе берём любого admin
            import json as _json
            admin = (session.query(User).filter(User.username == 'aleksandrinsider').first()
                     or session.query(User).filter_by(is_admin=True).first())
            if not admin:
                logger.warning("[ARENA] No author user found, skipping agent seed.")
                return

            for agent_data in ARENA_AGENTS:
                exists = session.query(UserAgent).filter_by(slug=agent_data["id"]).first()
                if exists:
                    continue
                ua = UserAgent(
                    author_id=admin.id,
                    name=agent_data["name"],
                    slug=agent_data["id"],
                    description=_build_description(agent_data),
                    specialization="arena",
                    personality=agent_data["system_prompt"],
                    tools_allowed=_json.dumps([]),
                    knowledge_base=_json.dumps([]),
                    price_per_message=0,  # бесплатно — арена
                    trial_messages=9999,
                    author_royalty_pct=0,
                    is_adult=False,
                    status="active",
                )
                session.add(ua)
                logger.info(f"[ARENA] Seeded agent: {agent_data['name']}")

            session.commit()
            logger.info("[ARENA] Test agents seeded successfully.")
        finally:
            session.close()
    except Exception as e:
        logger.error(f"[ARENA] Failed to seed agents: {e}")


def _build_description(agent: dict) -> str:
    """Возвращает описание агента (для маркетплейс-агентов)."""
    return agent.get('description') or agent.get('title', '')
