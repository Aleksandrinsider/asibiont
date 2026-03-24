"""
Векторная память на Pinecone — семантический поиск по истории пользователя.

Что хранит:
- Все значимые сообщения пользователя (цели, решения, инсайты)
- Факты из профиля
- Результаты исследований
- Эмоциональные паттерны

Как работает:
- При каждом сообщении — upsert embedding в Pinecone
- При генерации ответа — semantic search по контексту
- Результат вставляется в системный промпт как [СЕМАНТИЧЕСКАЯ ПАМЯТЬ]

Embeddings:
- Приоритет: OpenAI text-embedding-3-small (если OPENAI_API_KEY задан)
- Fallback: улучшенные pseudo-embeddings (50 категорий + TF-IDF bigrams)
- Dimension: 384 (сжатие для OpenAI, нативная для pseudo)

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
from datetime import datetime
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
                dimension=384,  # Размерность наших embeddings
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
# EMBEDDINGS — OpenAI (приоритет) + улучшенные pseudo (fallback)
# ═══════════════════════════════════════════════════════════════

# Расширенные семантические категории (50 тем) — гранулярнее = больше дискриминативности
SEMANTIC_CATEGORIES = {
    'разработка': ['код', 'баг', 'фича', 'api', 'разработка', 'программирование', 'деплой',
                   'git', 'коммит', 'рефакторинг', 'тестирование', 'frontend', 'backend', 'debug'],
    'стартап': ['стартап', 'mvp', 'продукт', 'запуск', 'pivot', 'юнит-экономика', 'traction',
                'выход на рынок', 'product-market fit', 'монетизация', 'growth'],
    'управление': ['проект', 'дедлайн', 'спринт', 'kanban', 'команда', 'менеджмент', 'планирование',
                   'приоритеты', 'делегирование', 'контроль', 'milestone'],
    'клиенты': ['клиент', 'заказчик', 'пользователь', 'отзыв', 'обратная связь', 'retention',
                'churn', 'ltv', 'лояльность', 'поддержка', 'саппорт'],
    'продажи': ['продажа', 'сделка', 'воронка', 'конверсия', 'лид', 'crm', 'холодный звонок',
                'коммерческое предложение', 'переговоры', 'закрытие', 'аутрич'],
    'маркетинг': ['маркетинг', 'реклама', 'таргет', 'smm', 'бренд', 'охват', 'виральность',
                  'позиционирование', 'промо', 'рекламный бюджет'],
    'контент': ['контент', 'пост', 'статья', 'видео', 'блог', 'newsletter', 'рассылка',
                'копирайтинг', 'сценарий', 'подкаст', 'сторис'],
    'соцсети': ['канал', 'телеграм', 'instagram', 'youtube', 'tiktok', 'подписчики',
                'аудитория', 'сообщество', 'вовлечённость', 'stories'],
    'финансы': ['деньги', 'бюджет', 'зарплата', 'доход', 'расход', 'прибыль', 'кэшфлоу',
                'налоги', 'бухгалтерия', 'финансовый план'],
    'инвестиции': ['инвестиции', 'инвестор', 'раунд', 'фондирование', 'венчур', 'ангел',
                   'акции', 'портфель', 'дивиденды', 'roi'],
    'крипто': ['крипто', 'биткоин', 'ethereum', 'defi', 'nft', 'токен', 'блокчейн',
               'майнинг', 'кошелёк', 'стейкинг', 'binance'],
    'здоровье': ['здоровье', 'спорт', 'тренировка', 'бег', 'зал', 'медитация',
                 'питание', 'диета', 'вес', 'анализы', 'врач'],
    'сон_энергия': ['сон', 'усталость', 'выгорание', 'энергия', 'отдых', 'бессонница',
                    'ранний подъём', 'режим', 'перерыв', 'восстановление'],
    'ментальное': ['стресс', 'тревога', 'депрессия', 'психология', 'терапия', 'mindfulness',
                   'фокус', 'прокрастинация', 'мотивация', 'привычка'],
    'отношения': ['семья', 'партнер', 'брак', 'дети', 'родители', 'конфликт',
                  'компромисс', 'разговор', 'границы'],
    'нетворкинг': ['нетворкинг', 'контакт', 'знакомство', 'коллега', 'встреча', 'конференция',
                   'менторство', 'коммьюнити', 'связи', 'рекомендация'],
    'обучение': ['учёба', 'курс', 'книга', 'навык', 'сертификат', 'обучение', 'лекция',
                 'мастер-класс', 'тренинг', 'практика'],
    'карьера': ['карьера', 'повышение', 'собеседование', 'резюме', 'оффер', 'увольнение',
                'фриланс', 'удалёнка', 'зарплата', 'должность'],
    'цели': ['цель', 'план', 'стратегия', 'результат', 'прогресс', 'достижение',
             'okr', 'kpi', 'milestone', 'roadmap'],
    'привычки': ['привычка', 'рутина', 'трекер', 'дисциплина', 'ежедневно', 'утренний ритуал',
                 'марафон', 'челлендж', 'стрик', 'регулярность'],
    'эмоции_позитив': ['рад', 'счастлив', 'воодушевлён', 'энтузиазм', 'благодарность',
                       'гордость', 'вдохновение', 'кураж', 'эйфория'],
    'эмоции_негатив': ['грустно', 'злюсь', 'разочарован', 'волнуюсь', 'боюсь', 'обидно',
                       'раздражение', 'апатия', 'безразличие', 'вина'],
    'ai_tech': ['ai', 'нейросеть', 'модель', 'gpt', 'deepseek', 'llm', 'промпт',
                'fine-tuning', 'rag', 'embeddings', 'трансформер', 'агент'],
    'автоматизация': ['автоматизация', 'бот', 'скрипт', 'интеграция', 'webhook', 'api',
                      'парсинг', 'cron', 'триггер', 'пайплайн'],
    'email': ['email', 'письмо', 'рассылка', 'почта', 'inbox', 'ответ', 'переписка',
              'спам', 'холодная рассылка', 'outreach', 'imap'],
    'дизайн': ['дизайн', 'ui', 'ux', 'макет', 'figma', 'прототип', 'логотип',
               'визуал', 'иллюстрация', 'брендбук'],
    'юридическое': ['договор', 'контракт', 'патент', 'лицензия', 'ip', 'юрист',
                    'суд', 'претензия', 'штраф', 'регистрация ооо'],
    'недвижимость': ['квартира', 'дом', 'аренда', 'ипотека', 'ремонт', 'переезд',
                     'офис', 'коворкинг', 'площадь'],
    'путешествия': ['путешествие', 'отпуск', 'перелёт', 'билет', 'отель', 'виза',
                    'командировка', 'релокация', 'страна', 'маршрут'],
    'еда': ['еда', 'рецепт', 'ресторан', 'доставка', 'готовка', 'кафе',
            'заказ', 'кухня', 'завтрак', 'ужин'],
}

# Кэш для OpenAI embeddings
_openai_available: bool | None = None  # None = не проверяли
_OPENAI_EMBED_MODEL = "text-embedding-3-small"
_OPENAI_EMBED_DIM = 384  # Сжимаем до 384 чтобы совпадало с Pinecone

EMBEDDING_DIM = 384


def _get_openai_embedding(text: str) -> list[float] | None:
    """Получает embedding через OpenAI API (синхронно).
    Возвращает None если ключ не задан или вызов провалился.
    """
    global _openai_available
    if _openai_available is False:
        return None
    
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        _openai_available = False
        return None
    
    try:
        import urllib.request
        import ssl
        
        payload = json.dumps({
            "model": _OPENAI_EMBED_MODEL,
            "input": text[:2000],
            "dimensions": _OPENAI_EMBED_DIM,
        })
        
        req = urllib.request.Request(
            "https://api.openai.com/v1/embeddings",
            data=payload.encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            embedding = result["data"][0]["embedding"]
            _openai_available = True
            return embedding
    except Exception as e:
        if _openai_available is None:
            logger.info("[VECTOR] OpenAI embeddings unavailable: %s — using pseudo", e)
            _openai_available = False
        return None


def _text_to_embedding(text: str) -> list[float]:
    """Создаёт embedding: OpenAI (если доступен) или улучшенный pseudo.
    
    Dimension: 384.
    """
    # Приоритет: OpenAI real embeddings
    real = _get_openai_embedding(text)
    if real:
        return real
    
    # Fallback: улучшенные pseudo-embeddings
    return _pseudo_embedding(text)


def _pseudo_embedding(text: str) -> list[float]:
    """Улучшенные pseudo-embeddings: 30 категорий + weighted bigram hashing.
    
    Dimension: 384 (60 category dims + 324 bigram hash dims).
    Значительно лучше trigram MD5: использует нормализованные веса и bigrams.
    """
    text_lower = text.lower()
    words = re.findall(r'\b[а-яёa-z]{2,}\b', text_lower)
    word_set = set(words)
    
    vec = [0.0] * EMBEDDING_DIM
    n_cats = len(SEMANTIC_CATEGORIES)
    
    # Первые 60 dims: 30 категорий × 2 (точное совпадение + substring)
    for i, (category, keywords) in enumerate(SEMANTIC_CATEGORIES.items()):
        if i >= 30:
            break
        # Точное совпадение слов
        exact_matches = len(word_set & set(keywords))
        vec[i * 2] = min(exact_matches / max(len(keywords) * 0.2, 1), 1.0)
        # Substring match (для составных слов)
        substr_matches = sum(1 for kw in keywords if kw in text_lower and kw not in word_set)
        vec[i * 2 + 1] = min(substr_matches / max(len(keywords) * 0.3, 1), 1.0)
    
    # Остальные 324 dims: weighted bigram hashing (лучше trigram MD5)
    bigram_offset = 60
    bigram_dims = EMBEDDING_DIM - bigram_offset  # 324 бина
    
    # Bigrams из слов (а не из символов) — семантически значимее
    word_bigrams = [f"{words[j]}_{words[j+1]}" for j in range(len(words) - 1)] if len(words) > 1 else []
    # Char trigrams для коротких текстов
    char_trigrams = [text_lower[j:j+3] for j in range(len(text_lower) - 2)]
    
    # TF-IDF подобное взвешивание: частые bigrams менее информативны
    bigram_counts = Counter(word_bigrams)
    for bg, count in bigram_counts.items():
        h = int(hashlib.md5(bg.encode()).hexdigest()[:10], 16) % bigram_dims
        # TF: log(1 + count), не линейное чтобы частые не доминировали
        weight = math.log1p(count) * 0.3
        vec[bigram_offset + h] = min(vec[bigram_offset + h] + weight, 1.0)
    
    # Char trigrams — вторичный сигнал с меньшим весом
    trigram_counts = Counter(char_trigrams)
    for tg, count in trigram_counts.items():
        h = int(hashlib.md5(tg.encode()).hexdigest()[:8], 16) % bigram_dims
        weight = math.log1p(count) * 0.08
        vec[bigram_offset + h] = min(vec[bigram_offset + h] + weight, 1.0)
    
    # L2 нормализация
    magnitude = sum(v ** 2 for v in vec) ** 0.5
    if magnitude > 0:
        vec = [v / magnitude for v in vec]
    
    return vec


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
        ts = datetime.utcnow().isoformat()
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


def _search_memory_sync(user_id, query, top_k=5):
    """Синхронная внутренняя версия — НЕ вызывать из async кода напрямую."""
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


async def search_memory(user_id, query, top_k=5):
    """Async-обёртка: семантический поиск без блокировки event loop."""
    try:
        return await asyncio.to_thread(_search_memory_sync, user_id, query, top_k)
    except Exception as e:
        logger.warning(f"[VECTOR] Async search failed: {e}")
        return []


def _build_memory_context_sync(user_id, current_message, max_chars=800):
    """Синхронный поиск памяти и формирование текстового контекста."""
    memories = _search_memory_sync(user_id, current_message, top_k=8)
    if not memories:
        return ""
    # Порог зависит от типа embedding: настоящие OpenAI-векторы точнее pseudo
    _threshold = 0.65 if _openai_available else 0.40
    # Цели и достижения показываем с пониженным порогом — они всегда релевантны
    _high_priority_types = {'goal', 'achievement', 'milestone', 'decision'}
    parts = []
    total = 0
    for m in memories:
        txt = m.get("text", "")
        mtype = m.get("type", "")
        effective_threshold = (_threshold - 0.10) if mtype in _high_priority_types else _threshold
        if txt and m.get("score", 0) > effective_threshold:
            prefix = "🎯" if mtype == "goal" else ("✅" if mtype == "achievement" else "—")
            entry = f"{prefix} {txt}"
            if total + len(entry) > max_chars:
                break
            parts.append(entry)
            total += len(entry)
    if not parts:
        return ""
    return "Из памяти:\n" + "\n".join(parts)


async def build_memory_context(user_id, current_message, max_chars=800):
    """Async-обёртка: строит контекст памяти без блокировки event loop."""
    try:
        return await asyncio.to_thread(_build_memory_context_sync, user_id, current_message, max_chars)
    except Exception as e:
        logger.warning(f"[VECTOR] Async memory context failed: {e}")
        return ""


def _store_conversation_turn_sync(user_id, user_message, bot_response, emotion=None, intent=None):
    """Синхронная внутренняя версия — сохраняет значимый обмен (не каждое сообщение).
    
    Фильтрует:
    - Слишком короткие сообщения (< 10 символов)
    - Технические команды
    - Дублирующие приветствия
    """
    # Фильтр: сохраняем только значимые сообщения
    if len(user_message) < 10:
        return False
    
    skip_patterns = ['привет', 'пока', 'ок', 'да', 'нет', 'ладно', 'спасибо', 'спс']
    if user_message.lower().strip() in skip_patterns:
        return False
    
    # Определяем тип контента
    content_type = "conversation"
    if any(w in user_message.lower() for w in ['цель', 'план', 'хочу', 'мечта', 'стремлюсь']):
        content_type = "goal"
    elif any(w in user_message.lower() for w in ['решил', 'принял решение', 'буду', 'сделаю']):
        content_type = "decision"
    elif any(w in user_message.lower() for w in ['узнал', 'понял', 'осознал', 'открытие']):
        content_type = "insight"
    elif any(w in user_message.lower() for w in ['устал', 'грустно', 'рад', 'злюсь', 'боюсь']):
        content_type = "emotion"
    
    metadata = {
        "type": content_type,
        "emotion": emotion or "neutral",
        "intent": intent or "general",
        "response_preview": bot_response[:100] if bot_response else "",
    }
    
    return _store_memory_sync(user_id, user_message, metadata)


async def store_conversation_turn(user_id, user_message, bot_response, emotion=None, intent=None):
    """Async-обёртка: сохраняет обмен без блокировки event loop."""
    try:
        return await asyncio.to_thread(
            _store_conversation_turn_sync, user_id, user_message, bot_response, emotion, intent
        )
    except Exception as e:
        logger.warning(f"[VECTOR] Async store turn failed: {e}")
        return False


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
