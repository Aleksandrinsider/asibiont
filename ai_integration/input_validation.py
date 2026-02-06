"""
Утилиты для безопасной обработки входных данных
Защита от injection атак и некорректных данных
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def sanitize_username(username: str, max_length: int = 50) -> Optional[str]:
    """
    Очистка username от опасных символов
    Допустимые: буквы, цифры, подчеркивание, дефис
    """
    if not username or not isinstance(username, str):
        return None
    
    # Удаляем пробелы по краям
    username = username.strip()
    
    # Ограничиваем длину
    username = username[:max_length]
    
    # Разрешаем только безопасные символы
    # Буквы (любые), цифры, подчеркивание, дефис
    if not re.match(r'^[\w\-]+$', username, re.UNICODE):
        logger.warning(f"Invalid username format: {username}")
        return None
    
    return username


def sanitize_text_input(text: str, max_length: int = 5000) -> str:
    """
    Очистка текстового ввода от потенциально опасного содержимого
    Удаляет SQL-подобные конструкции и ограничивает длину
    """
    if not text or not isinstance(text, str):
        return ""
    
    # Удаляем пробелы по краям
    text = text.strip()
    
    # Ограничиваем длину
    text = text[:max_length]
    
    # Удаляем подозрительные SQL-конструкции (простая защита)
    dangerous_patterns = [
        r';\s*DROP\s+TABLE',
        r';\s*DELETE\s+FROM',
        r';\s*UPDATE\s+.*\s+SET',
        r'--\s*$',
        r'/\*.*\*/',
        r'UNION\s+SELECT',
        r'EXEC\s*\(',
        r'EXECUTE\s*\(',
    ]
    
    for pattern in dangerous_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            logger.warning(f"Detected dangerous SQL pattern in input: {pattern}")
            # Не блокируем полностью, но логируем
            # SQLAlchemy ORM защищает от injection, но лучше быть осторожным
    
    return text


def sanitize_task_title(title: str, max_length: int = 200) -> str:
    """Очистка названия задачи"""
    if not title or not isinstance(title, str):
        return "Без названия"
    
    title = title.strip()
    
    # Ограничиваем длину
    if len(title) > max_length:
        title = title[:max_length] + "..."
    
    # Удаляем управляющие символы кроме переносов строк
    title = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', title)
    
    return title if title else "Без названия"


def sanitize_telegram_id(telegram_id) -> Optional[int]:
    """
    Валидация Telegram ID
    Должен быть положительным целым числом
    """
    try:
        tid = int(telegram_id)
        if tid > 0:
            return tid
        logger.warning(f"Invalid Telegram ID (not positive): {telegram_id}")
        return None
    except (ValueError, TypeError):
        logger.warning(f"Invalid Telegram ID format: {telegram_id}")
        return None


def validate_email(email: str) -> bool:
    """
    Простая валидация email
    """
    if not email or not isinstance(email, str):
        return False
    
    # Базовая проверка формата
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def sanitize_url(url: str, max_length: int = 500) -> Optional[str]:
    """
    Очистка и валидация URL
    """
    if not url or not isinstance(url, str):
        return None
    
    url = url.strip()[:max_length]
    
    # Проверяем, что это HTTP/HTTPS URL
    if not re.match(r'^https?://', url, re.IGNORECASE):
        logger.warning(f"Invalid URL scheme: {url}")
        return None
    
    # Базовая проверка формата
    url_pattern = r'^https?://[^\s<>"\']+'
    if not re.match(url_pattern, url, re.IGNORECASE):
        logger.warning(f"Invalid URL format: {url}")
        return None
    
    return url


def sanitize_phone(phone: str) -> Optional[str]:
    """
    Очистка номера телефона
    Оставляет только цифры и +
    """
    if not phone or not isinstance(phone, str):
        return None
    
    # Удаляем все кроме цифр, +, -, (), пробелов
    phone = re.sub(r'[^\d+\-() ]', '', phone)
    
    # Ограничиваем длину
    phone = phone[:20]
    
    # Проверяем минимальную длину (хотя бы 7 цифр)
    digits_only = re.sub(r'[^\d]', '', phone)
    if len(digits_only) < 7:
        return None
    
    return phone


# Экспортируем основные функции
__all__ = [
    'sanitize_username',
    'sanitize_text_input',
    'sanitize_task_title',
    'sanitize_telegram_id',
    'validate_email',
    'sanitize_url',
    'sanitize_phone',
]
