"""
AI-powered flexible time parser using DeepSeek
"""
import logging
from datetime import datetime, timedelta
import pytz
import json
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
import requests

logger = logging.getLogger(__name__)


def parse_time_with_ai(time_str: str, current_time: datetime) -> datetime | None:
    """
    Использует DeepSeek для парсинга любого формата времени.
    
    Args:
        time_str: Строка со временем ("завтра в 10:00", "через 2 часа", "15:30", etc)
        current_time: Текущее время в timezone пользователя
    
    Returns:
        datetime в timezone пользователя или None если не удалось распарсить
    """
    try:
        current_str = current_time.strftime('%Y-%m-%d %H:%M')
        weekday_ru = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье']
        current_weekday = weekday_ru[current_time.weekday()]
        
        prompt = f"""Текущее время: {current_str} ({current_weekday})

Пользователь хочет перенести задачу на: "{time_str}"

Верни JSON с целевым временем:
{{
  "year": 2026,
  "month": 1,
  "day": 28,
  "hour": 10,
  "minute": 0
}}

Правила:
- Если указано только время (HH:MM) без даты - используй сегодня, если время не прошло, иначе завтра
- "завтра" = +1 день от текущей даты
- "послезавтра" = +2 дня
- "через N часов/минут" = прибавь к текущему времени
- Если не можешь распарсить - верни {{"error": "описание проблемы"}}

Верни ТОЛЬКО JSON, без текста."""

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": DEEPSEEK_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 150,
            "temperature": 0.0
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=10)
        
        if response.status_code != 200:
            logger.error(f"DeepSeek API error: {response.status_code}")
            return None
        
        result = response.json()
        content = result["choices"][0]["message"]["content"].strip()
        
        # Remove markdown code blocks if present
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        content = content.strip()
        
        parsed = json.loads(content)
        
        if "error" in parsed:
            logger.warning(f"AI couldn't parse time '{time_str}': {parsed['error']}")
            return None
        
        # Create datetime in user's timezone
        user_tz = current_time.tzinfo
        result_dt = current_time.replace(
            year=parsed["year"],
            month=parsed["month"],
            day=parsed["day"],
            hour=parsed["hour"],
            minute=parsed["minute"],
            second=0,
            microsecond=0
        )
        
        logger.info(f"✅ AI parsed '{time_str}' → {result_dt}")
        return result_dt
        
    except Exception as e:
        logger.error(f"❌ AI time parsing failed: {e}")
        return None


def parse_time_simple_fallback(time_str: str, current_time: datetime) -> datetime | None:
    """
    Простой fallback для базовых форматов если AI не доступен.
    Парсит только HH:MM.
    """
    try:
        # Only HH:MM format
        if ':' in time_str:
            time_part = time_str.strip()
            parts = time_part.split(':')
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                hour = int(parts[0])
                minute = int(parts[1])
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    result = current_time.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    # If time passed, schedule for tomorrow
                    if result <= current_time:
                        result += timedelta(days=1)
                    logger.info(f"✅ Simple fallback parsed '{time_str}' → {result}")
                    return result
    except Exception as e:
        logger.error(f"❌ Simple fallback failed: {e}")
    
    return None
