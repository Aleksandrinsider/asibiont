"""
AI-powered flexible time parser using DeepSeek
"""
import logging
from datetime import datetime, timedelta
import pytz
import json
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
import aiohttp
import asyncio

logger = logging.getLogger(__name__)


async def parse_time_with_ai(time_str: str, current_time: datetime) -> datetime | None:
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

Верни JSON с целевым временем в формате:
{{
  "year": число,
  "month": число,
  "day": число,
  "hour": число,
  "minute": число
}}

Правила:
- Если указано только время (HH:MM) без даты - используй сегодня, если время не прошло, иначе завтра
- "завтра" = +1 день от текущей даты
- "послезавтра" = +2 дня
- "через N часов/минут" = прибавь к текущему времени
- "сегодня" = текущая дата
- "каждый день" = игнорируй, используй указанное время на сегодня
- Если не можешь распарсить - верни {{"error": "описание проблемы"}}

Примеры:
- "завтра в 10:00" → {{"year": {current_time.year}, "month": {current_time.month}, "day": {current_time.day + 1}, "hour": 10, "minute": 0}}
- "сегодня в 18:00" → {{"year": {current_time.year}, "month": {current_time.month}, "day": {current_time.day}, "hour": 18, "minute": 0}}
- "через 2 часа" → рассчитай от текущего времени

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
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status != 200:
                    logger.error(f"DeepSeek API error: {response.status}")
                    return None
                
                result = await response.json()
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
        
        # Create datetime in user's timezone with proper error handling
        try:
            result_dt = current_time.replace(
                year=int(parsed["year"]),
                month=int(parsed["month"]),
                day=int(parsed["day"]),
                hour=int(parsed["hour"]),
                minute=int(parsed["minute"]),
                second=0,
                microsecond=0
            )
            
            logger.info(f"✅ AI parsed '{time_str}' → {result_dt}")
            return result_dt
        except (ValueError, KeyError) as e:
            logger.error(f"❌ Invalid date/time from AI: {parsed}, error: {e}")
            return None
        
    except Exception as e:
        logger.error(f"❌ AI time parsing failed: {e}")
        return None


def parse_time_simple_fallback(time_str: str, current_time: datetime) -> datetime | None:
    """
    Простой fallback для базовых форматов если AI не доступен.
    Парсит HH:MM, "через N минут/часов/дней".
    """
    import re
    try:
        time_str = time_str.lower().strip()
        
        # "через N минут/часов/дней"
        через_match = re.match(r'через (\d+) (минут|час|часа|часов|дней|дня)', time_str)
        if через_match:
            num = int(через_match.group(1))
            unit = через_match.group(2)
            if 'минут' in unit:
                delta = timedelta(minutes=num)
            elif 'час' in unit:
                delta = timedelta(hours=num)
            elif 'дней' in unit or 'дня' in unit:
                delta = timedelta(days=num)
            result = current_time + delta
            logger.info(f"✅ Simple fallback parsed '{time_str}' → {result}")
            return result
        
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


def parse_time(time_str: str, timezone_str: str = 'UTC') -> datetime | None:
    """
    Универсальная функция парсинга времени.
    Использует AI для сложных форматов, fallback для простых.
    """
    try:
        # Get current time in specified timezone
        if timezone_str == 'UTC':
            tz = pytz.UTC
        else:
            tz = pytz.timezone(timezone_str)
        
        current_time = datetime.now(tz)
        
        # Try AI parsing first
        result = parse_time_with_ai(time_str, current_time)
        if result:
            return result
        
        # Fallback to simple parsing
        return parse_time_simple_fallback(time_str, current_time)
        
    except Exception as e:
        logger.error(f"Error in parse_time: {e}")
        return None
