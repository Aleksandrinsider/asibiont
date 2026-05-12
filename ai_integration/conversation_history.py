"""
Conversation history management for context-aware AI responses
"""

import json
import logging
import re
from datetime import datetime, timezone
from models import Session, User

logger = logging.getLogger(__name__)

MAX_HISTORY_MESSAGES = 10000  # Храним всю историю без ограничений (БД хранит всё, AI получает последние N через limit=)


def _smart_truncate(content: str, role: str) -> str:
    """Smart truncation: keeps beginning + end for long assistant messages.
    Short messages pass through unchanged. Long assistant answers preserve
    first 800 chars (context/result summary) + last 300 chars (next steps).
    
    For tool-annotated responses (starting with [Действия: ...]), preserves
    the tool annotation and then applies truncation to the rest.
    """
    max_len = 1200 if role == 'assistant' else 800
    if len(content) <= max_len:
        return content
    
    # Check for tool annotation prefix
    _tool_anno_match = re.match(r'^(\[Действия:[^\]]+\]\n)', content)
    anno = _tool_anno_match.group(1) if _tool_anno_match else ''
    body = content[len(anno):] if anno else content
    
    if role == 'assistant' and body:
        keep_start = 800
        keep_end = 300
        truncated = body[:keep_start] + '\n…[сокращено]…\n' + body[-keep_end:]
        return anno + truncated if anno else truncated
    
    return anno + body[:max_len] if anno else content[:max_len]


def _summarize_long_dialogue(history: list[dict], max_messages: int = 20) -> list[dict]:
    """Сжимает длинную историю диалога: оставляет последние N сообщений
    и добавляет краткое саммари более старых.

    Для контекстов >40 сообщений: первые (len-20) сообщений → саммари,
    последние 20 → полные.

    Args:
        history: список сообщений [{role, content, timestamp}, ...]
        max_messages: сколько последних сообщений оставить полными

    Returns:
        список сообщений с возможным саммари в начале
    """
    if len(history) <= max_messages + 5:
        return history  # не нужно сжимать

    # Берём последние max_messages сообщений полными
    recent = history[-max_messages:]

    # Старые сообщения (>max_messages назад) → саммари
    old = history[:-max_messages]

    # Строим саммари: считаем количество по ролям и основные темы
    user_count = sum(1 for m in old if m.get('role') == 'user')
    assistant_count = sum(1 for m in old if m.get('role') == 'assistant')

    # Извлекаем темы
    all_old_text = ' '.join(m.get('content', '')[:200] for m in old[-30:])
    topic_signals = {
        'финансы/инвестиции': ['инвестици', 'акци', 'портфель', 'диверсифициру'],
        'криптовалюта': ['биткоин', 'крипт', 'eth', 'btc', 'токен'],
        'бизнес/стартап': ['стартап', 'бизнес', 'продукт', 'монетизаци'],
        'маркетинг': ['маркетинг', 'продаж', 'лид', 'таргет'],
        'здоровье': ['здоровь', 'фитнес', 'спорт', 'тренировк'],
        'технологии': ['нейросет', 'ai', 'программирован', 'код'],
        'email/рассылки': ['email', 'рассылки', 'outreach', 'кампани'],
        'агенты/делегирование': ['агент', 'делегирова', 'автопилот'],
    }
    found_topics = []
    for topic, keywords in topic_signals.items():
        if any(kw in all_old_text.lower() for kw in keywords):
            found_topics.append(topic)

    summary_text = (
        f"[Предыдущий диалог: {user_count} сообщений пользователя, "
        f"{assistant_count} ответов ассистента"
    )
    if found_topics:
        summary_text += f". Темы: {', '.join(found_topics)}"
    summary_text += "]"

    # Помещаем саммари перед свежими сообщениями
    result = [{'role': 'system', 'content': summary_text}] + recent
    logger.info(f"[HISTORY] Compressed {len(old)} old messages into summary for {len(recent)} recent")
    return result


def extract_key_facts_from_history(history: list[dict], max_facts: int = 5) -> list[str]:
    """Извлекает ключевые факты из истории диалога: решения, цели, предпочтения пользователя.

    Анализирует сообщения пользователя на предмет:
    - Решений ('давай', 'начнём', 'сделаем', 'согласен')
    - Целей ('хочу', 'планирую', 'моя цель')
    - Предпочтений ('лучше', 'нравится', 'предпочитаю')
    - Инсайтов ('понял', 'осознал')
    - Достижений ('сделал', 'готово', 'закончил')

    Args:
        history: список сообщений [{role, content}, ...]
        max_facts: максимальное количество фактов для возврата

    Returns:
        список строк-фактов
    """
    facts = []
    seen = set()

    for msg in history:
        if msg.get('role') != 'user':
            continue
        text = msg.get('content', '')
        text_lower = text.lower()

        # Решения
        if any(w in text_lower for w in ['давай', 'начн', 'сделаем', 'попробу', 'согласен', 'ok,', 'okay']):
            fact = f"User decision: {text[:150]}"
            if fact not in seen:
                facts.append(fact)
                seen.add(fact)

        # Цели
        if any(w in text_lower for w in ['хочу', 'планирую', 'мечтаю', 'моя цель', 'моя задача']):
            fact = f"User goal: {text[:150]}"
            if fact not in seen:
                facts.append(fact)
                seen.add(fact)

        # Предпочтения
        if any(w in text_lower for w in ['лучше', 'нравится', 'предпочитаю', 'не люблю', 'не нравится']):
            fact = f"User preference: {text[:150]}"
            if fact not in seen:
                facts.append(fact)
                seen.add(fact)

        # Инсайты
        if any(w in text_lower for w in ['понял', 'осознал', 'вот в чём дело', 'теперь ясно']):
            fact = f"User insight: {text[:150]}"
            if fact not in seen:
                facts.append(fact)
                seen.add(fact)

        if len(facts) >= max_facts:
            break

    return facts[:max_facts]


# Паттерны фраз, которые могут содержать галлюцинированные данные о задачах
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


def sanitize_token_hallucinations(text: str) -> str:
    """Исправляет галлюцинированные суммы токенов в любом тексте (посты, email, TG)."""
    if not text:
        return text
    for pattern, replacement in _TOKEN_HALLUCINATION_REPLACEMENTS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text



def _sanitize_assistant_message(content):
    """Убирает из ответов ассистента ложные утверждения о задачах и неправильные суммы токенов."""
    if not content:
        return content

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

        # Add new message — храним полный контент (не обрезаем при сохранении)
        message = {
            "role": role,
            "content": content,
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
            
            # Сжатие длинных диалогов: если >40 сообщений → саммари старых
            if limit and limit > 20 and len(history) > 40:
                history = _summarize_long_dialogue(history, max_messages=limit // 2)
            elif len(history) > 20:
                # Даже без limit, если история длинная — сжимаем
                history = _summarize_long_dialogue(history, max_messages=15)

            # Return only role and content for AI (with sanitization)
            # Обрезаем каждое сообщение только здесь (при отдаче в LLM), не при сохранении
            result = []
            for msg in history:
                content = msg.get("content", "")
                role = msg.get("role", "user")
                # Санитизируем ответы ассистента — убираем галлюцинации о задачах
                if role == "assistant":
                    content = _sanitize_assistant_message(content)
                # Обрезка для LLM-контекста: храним полное, отдаём разумное
                content = _smart_truncate(content, role)
                result.append({"role": role, "content": content})

            # Добавляем ключевые факты (извлечённые решения/предпочтения) если история достаточно длинная
            if len(history) >= 10:
                try:
                    key_facts = extract_key_facts_from_history(history, max_facts=3)
                    if key_facts:
                        facts_block = "Key facts from history:\n" + "\n".join(key_facts)
                        result.insert(0, {"role": "system", "content": facts_block})
                except Exception as _kf_err:
                    logger.debug(f"[HISTORY] Key facts extraction failed: {_kf_err}")

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
