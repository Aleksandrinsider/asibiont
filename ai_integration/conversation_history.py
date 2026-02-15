"""
Conversation history management for context-aware AI responses
"""

import json
import logging
from datetime import datetime, timezone
from models import Session, User

logger = logging.getLogger(__name__)

MAX_HISTORY_MESSAGES = 24  # Keep last 24 messages (12 exchanges) for topic extraction


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
        max_len = 2000 if role == 'assistant' else 1200
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
            
            # Apply limit if specified
            if limit and len(history) > limit:
                history = history[-limit:]
            
            # Return only role and content for AI
            return [{"role": msg["role"], "content": msg["content"]} for msg in history]
            
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


def get_last_assistant_message(user_id, session=None):
    """
    Get the last assistant message for context
    Useful for understanding what AI just proposed/offered
    """
    history = get_conversation_history(user_id, session, limit=5)
    
    # Find last assistant message
    for msg in reversed(history):
        if msg["role"] == "assistant":
            return msg["content"]
    
    return None
