"""
Conversation history management for context-aware AI responses
"""

import json
import logging
import re
from datetime import datetime, timezone
from models import Session, User

logger = logging.getLogger(__name__)

MAX_HISTORY_MESSAGES = 16  # Keep last 16 messages (8 exchanges) for topic extraction

# Паттерны фраз, которые могут содержать галлюцинированные данные о задачах
# Эти фразы в сообщениях ассистента будут удалены при загрузке истории
_HALLUCINATION_PATTERNS = [
    r'[уУ] тебя есть задач[аи].*?(?:в \d{1,2}:\d{2}|на завтра|на сегодня|на \d)',
    r'[вВ]ижу,? что (?:у тебя|ты).*?задач[аи]',
    r'[нН]е забудь (?:про|о) задач[уе]',
    r'[зЗ]адача.*?по (?:поиску|созданию|разработке|написанию)',
    r'[тТ]вой план на (?:завтра|сегодня|неделю).*?задач',
    r'[уУ] тебя (?:уже )?есть цель.*?(?:«|\")',
    r'[вВ]ижу.*?цел[ьи].*?(?:«|\")',
    r'[цЦ]ел[ьи].*?[Тт]естирование гипотез',
]

# Паттерны неправильных сумм токенов (галлюцинации AI)
_TOKEN_HALLUCINATION_REPLACEMENTS = [
    # "1000 + 500" / "1000+500" / "1000 токенов + 500 бонусных" и т.п.
    (r'1[.,\s]*000\s*(?:токенов\s*)?[+＋]\s*500\s*(?:бонусных\s*)?(?:токенов)?', '1500 токенов'),
    # "500 токенов за каждого приглашённого/реферала"
    (r'500\s*токенов\s*за\s*(?:каждого\s*)?(?:приглашённого|реферала|друга|пользователя|привлечённого)',
     '20% от каждого пополнения приглашённого друга'),
]


# Feminine verb endings that leak agent persona into ASI context
_FEMININE_TO_NEUTRAL = [
    (r'\b([Яя])\s+(нашла|проверила|отправила|сделала|написала|создала|удалила|обновила|загрузила|подготовила|исследовала|проанализировала|собрала|завершила|добавила|получила|увидела|поняла|решила|опубликовала)\b',
     lambda m: m.group(1) + ' ' + re.sub(r'ла$', 'л', m.group(2))),
]


def _sanitize_assistant_message(content):
    """Убирает из ответов ассистента ложные утверждения о задачах, неправильные суммы токенов, и женские глагольные формы (утечка persona агента)."""
    if not content:
        return content
    
    # Нейтрализуем женские глагольные формы (агент Кристина → ASI мужской род)
    for pattern, repl in _FEMININE_TO_NEUTRAL:
        content = re.sub(pattern, repl, content)

    # Исправляем галлюцинированные суммы токенов
    for pattern, replacement in _TOKEN_HALLUCINATION_REPLACEMENTS:
        content = re.sub(pattern, replacement, content, flags=re.IGNORECASE)
    
    # Разбиваем на предложения
    sentences = re.split(r'(?<=[.!?])\s+', content)
    cleaned = []
    removed = False
    
    for sentence in sentences:
        is_hallucination = False
        for pattern in _HALLUCINATION_PATTERNS:
            if re.search(pattern, sentence, re.IGNORECASE):
                is_hallucination = True
                removed = True
                break
        if not is_hallucination:
            cleaned.append(sentence)
    
    if removed:
        result = ' '.join(cleaned).strip()
        logger.info(f"[HISTORY] Sanitized assistant message, removed hallucinated task references")
        return result if result else "Привет!"
    
    return content


def save_message_to_history(user_id, role, content, session=None):
    """
    Save message to user's conversation history
    
    Args:
        user_id: Telegram user ID
        role: 'user' or 'assistant'
        content: Message content
        session: DB session (optional)
    """
    logger.info(f"[HISTORY] Attempting to save {role} message for user {user_id}")
    
    should_close = False
    if session is None:
        session = Session()
        should_close = True
        logger.info(f"[HISTORY] Created new session")
    
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            logger.warning(f"[HISTORY] User {user_id} not found")
            return
        
        logger.info(f"[HISTORY] Found user {user.username} (ID: {user.id})")
        
        # Load existing history
        history = []
        if user.conversation_context:
            try:
                history = json.loads(user.conversation_context)
            except json.JSONDecodeError:
                logger.error(f"[HISTORY] Failed to parse conversation_context for user {user_id}")
                history = []
        
        # Add new message
        # Ассистент пишет длиннее — даём больше места для контекста
        max_len = 800 if role == 'assistant' else 600
        message = {
            "role": role,
            "content": content[:max_len],
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        history.append(message)
        
        # Keep only last N messages
        if len(history) > MAX_HISTORY_MESSAGES:
            history = history[-MAX_HISTORY_MESSAGES:]
        
        # Save back to DB
        user.conversation_context = json.dumps(history, ensure_ascii=False)
        session.commit()
        
        logger.info(f"[HISTORY] Saved {role} message for user {user_id}, history length: {len(history)}")
        
    except Exception as e:
        logger.error(f"[HISTORY] Error saving message: {e}")
        if session:
            session.rollback()
    finally:
        if should_close:
            session.close()


def get_conversation_history(user_id, session=None, limit=None):
    """
    Get conversation history for user
    
    Args:
        user_id: Telegram user ID
        session: DB session (optional)
        limit: Maximum number of messages to return
    
    Returns:
        List of message dicts with 'role' and 'content'
    """
    should_close = False
    if session is None:
        session = Session()
        should_close = True
    
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user or not user.conversation_context:
            return []
        
        try:
            history = json.loads(user.conversation_context)
            
            # Filter out messages before history_cleared_at (failsafe)
            if user.history_cleared_at:
                cleared_ts = user.history_cleared_at.replace(tzinfo=timezone.utc).isoformat() \
                    if user.history_cleared_at.tzinfo is None \
                    else user.history_cleared_at.isoformat()
                history = [
                    m for m in history
                    if m.get('timestamp', '') >= cleared_ts
                ]
            
            # Apply limit if specified
            if limit and len(history) > limit:
                history = history[-limit:]
            
            # Return only role and content for AI (with sanitization)
            result = []
            for msg in history:
                content = msg["content"]
                role = msg["role"]
                # Санитизируем ответы ассистента — убираем галлюцинации о задачах
                if role == "assistant":
                    content = _sanitize_assistant_message(content)
                result.append({"role": role, "content": content})
            return result
            
        except json.JSONDecodeError:
            logger.error(f"[HISTORY] Failed to parse conversation_context for user {user_id}")
            return []
            
    except Exception as e:
        logger.error(f"[HISTORY] Error getting history: {e}")
        return []
    finally:
        if should_close:
            session.close()


def clear_conversation_history(user_id, session=None):
    """Clear conversation history for user"""
    should_close = False
    if session is None:
        session = Session()
        should_close = True
    
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            user.conversation_context = None
            user.history_cleared_at = datetime.now(timezone.utc)
            session.commit()
            logger.info(f"[HISTORY] Cleared history for user {user_id}")
    except Exception as e:
        logger.error(f"[HISTORY] Error clearing history: {e}")
        if session:
            session.rollback()
    finally:
        if should_close:
            session.close()


