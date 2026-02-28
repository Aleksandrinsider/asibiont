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

ВАЖНО: Все Pinecone-операции (upsert, query) — синхронные HTTP-вызовы.
Публичные async-обёртки используют asyncio.to_thread() чтобы не блокировать event loop.
"""

import os
import json
import asyncio
import hashlib
import logging
import re
from datetime import datetime

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


# ═══════════════════════════════════════════════════════════════
# LIGHTWEIGHT EMBEDDINGS (без внешних моделей)
# ═══════════════════════════════════════════════════════════════

# Словарь семантических категорий для pseudo-embeddings
SEMANTIC_CATEGORIES = {
    'работа': ['работа', 'проект', 'задача', 'дедлайн', 'клиент', 'релиз', 'код', 'баг', 'фича', 'api',
               'разработка', 'программирование', 'стартап', 'продукт', 'mvp', 'запуск', 'команда'],
    'здоровье': ['здоровье', 'спорт', 'тренировка', 'бег', 'зал', 'сон', 'усталость', 'выгорание',
                 'медитация', 'питание', 'диета', 'вес', 'энергия', 'отдых'],
    'финансы': ['деньги', 'инвестиции', 'бюджет', 'зарплата', 'доход', 'расход', 'прибыль', 'кредит',
                'фондирование', 'раунд', 'инвестор', 'выручка', 'подписка', 'оплата'],
    'отношения': ['друг', 'семья', 'партнер', 'коллега', 'конфликт', 'общение', 'поддержка',
                  'одиночество', 'встреча', 'знакомство', 'нетворкинг', 'контакт'],
    'обучение': ['учёба', 'курс', 'книга', 'навык', 'опыт', 'урок', 'лекция', 'практика',
                 'сертификат', 'обучение', 'развитие', 'рост', 'менторство'],
    'эмоции': ['рад', 'грустно', 'устал', 'злюсь', 'волнуюсь', 'боюсь', 'счастлив', 'мотивация',
               'выгорание', 'стресс', 'тревога', 'спокойствие', 'энтузиазм'],
    'цели': ['цель', 'план', 'стратегия', 'миссия', 'видение', 'достижение', 'результат',
             'прогресс', 'шаг', 'этап', 'марафон', 'привычка', 'рутина'],
    'ai_tech': ['ai', 'нейросеть', 'агент', 'модель', 'gpt', 'deepseek', 'llm', 'промпт',
                'тренировка модели', 'fine-tuning', 'rag', 'embeddings', 'трансформер'],
    'маркетинг': ['маркетинг', 'контент', 'пост', 'канал', 'аудитория', 'воронка', 'конверсия',
                  'бренд', 'реклама', 'smm', 'таргет', 'продвижение', 'виральность'],
    'быт': ['погода', 'дом', 'покупка', 'ремонт', 'переезд', 'путешествие', 'отпуск',
            'еда', 'рецепт', 'машина', 'доставка', 'квартира'],
}

# Количество категорий определяет размерность вектора
EMBEDDING_DIM = 384  # Фиксированная размерность


def _text_to_embedding(text):
    """Создаёт pseudo-embedding на основе семантических категорий + char n-grams.
    
    Не использует внешние модели — работает мгновенно.
    Dimension: 384 (10 категорий * 2 + 364 char trigram hash features).
    """
    text_lower = text.lower()
    words = set(re.findall(r'\b[а-яёa-z]{3,}\b', text_lower))
    
    vec = [0.0] * EMBEDDING_DIM
    
    # Первые 20 dims: семантические категории (10 категорий × 2)
    for i, (category, keywords) in enumerate(SEMANTIC_CATEGORIES.items()):
        matches = sum(1 for kw in keywords if kw in text_lower)
        # Нормализуем: сколько % ключевых слов совпало
        score = min(matches / max(len(keywords) * 0.3, 1), 1.0)
        vec[i * 2] = score
        vec[i * 2 + 1] = min(len(words & set(keywords)) / 3.0, 1.0)
    
    # Остальные 364 dims: character trigram hashing
    trigrams = [text_lower[j:j+3] for j in range(len(text_lower) - 2)]
    for tg in trigrams:
        h = int(hashlib.md5(tg.encode()).hexdigest()[:8], 16) % 364
        vec[20 + h] = min(vec[20 + h] + 0.15, 1.0)
    
    # Нормализация вектора (L2)
    magnitude = sum(v**2 for v in vec) ** 0.5
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
    memories = _search_memory_sync(user_id, current_message, top_k=5)
    if not memories:
        return ""
    parts = []
    total = 0
    for m in memories:
        txt = m.get("text", "")
        if txt and m.get("score", 0) > 0.4:
            entry = f"— {txt}"
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
