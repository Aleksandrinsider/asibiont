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

EDGE CASES (особые случаи):
- "утром" = 9:00 сегодня или завтра если утро прошло
- "днем" = 14:00 сегодня или завтра если день прошел
- "вечером" = 19:00 сегодня или завтра если вечер прошел
- "ночью" = 23:00 сегодня или завтра если ночь прошла
- "сейчас" = текущее время + 5 минут
- "прямо сейчас" = текущее время + 1 минута
- "скоро" = текущее время + 30 минут
- "позже" = текущее время + 2 часа
- "в обед" = 13:00 сегодня или завтра
- "после обеда" = 15:00 сегодня или завтра
- "до обеда" = 11:00 сегодня или завтра
- "на выходных" = ближайшая суббота 10:00
- "в понедельник" = следующий понедельник 9:00
- "в пятницу вечером" = ближайшая пятница 19:00
- "через неделю" = +7 дней, то же время
- "в конце месяца" = последний день месяца 17:00
- "в начале месяца" = 1 число следующего месяца 9:00

Примеры:
- "завтра в 10:00" → {{"year": {current_time.year}, "month": {current_time.month}, "day": {current_time.day + 1}, "hour": 10, "minute": 0}}
- "сегодня в 18:00" → {{"year": {current_time.year}, "month": {current_time.month}, "day": {current_time.day}, "hour": 18, "minute": 0}}
- "через 2 часа" → рассчитай от текущего времени
- "утром" → {{"year": {current_time.year}, "month": {current_time.month}, "day": {current_time.day}, "hour": 9, "minute": 0}} (или завтра если сейчас после 12)
- "в понедельник" → следующий понедельник 9:00
- "скоро" → текущее время + 30 минут

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
    Парсит: "завтра в HH:MM", "сегодня в HH:MM", "послезавтра в HH:MM",
            "через N минут/часов/дней", "HH:MM".
    """
    import re
    try:
        time_str = time_str.lower().strip()
        
        # Helper: extract HH:MM from string
        def _extract_hhmm(s):
            m = re.search(r'(\d{1,2}):(\d{2})', s)
            if m:
                h, mi = int(m.group(1)), int(m.group(2))
                if 0 <= h <= 23 and 0 <= mi <= 59:
                    return h, mi
            return None, None
        
        # "через N минут/часов/дней/недель"
        через_match = re.match(r'через\s+(\d+)\s+(минут|мин|час|часа|часов|дней|дня|день|недел|нед)', time_str)
        if через_match:
            num = int(через_match.group(1))
            unit = через_match.group(2)
            if 'минут' in unit or 'мин' in unit:
                delta = timedelta(minutes=num)
            elif 'час' in unit:
                delta = timedelta(hours=num)
            elif 'недел' in unit or 'нед' in unit:
                delta = timedelta(weeks=num)
            else:
                delta = timedelta(days=num)
            result = current_time + delta
            logger.info(f"✅ Simple fallback parsed '{time_str}' → {result}")
            return result
        
        # "завтра [в] HH:MM"
        if time_str.startswith('завтра'):
            h, mi = _extract_hhmm(time_str)
            if h is not None:
                result = (current_time + timedelta(days=1)).replace(hour=h, minute=mi, second=0, microsecond=0)
                logger.info(f"✅ Simple fallback parsed '{time_str}' → {result}")
                return result
            else:
                # "завтра" without time → 09:00
                result = (current_time + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
                logger.info(f"✅ Simple fallback parsed '{time_str}' → {result}")
                return result
        
        # "послезавтра [в] HH:MM"
        if time_str.startswith('послезавтра'):
            h, mi = _extract_hhmm(time_str)
            if h is not None:
                result = (current_time + timedelta(days=2)).replace(hour=h, minute=mi, second=0, microsecond=0)
                logger.info(f"✅ Simple fallback parsed '{time_str}' → {result}")
                return result
            else:
                result = (current_time + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
                logger.info(f"✅ Simple fallback parsed '{time_str}' → {result}")
                return result
        
        # "сегодня [в] HH:MM"
        if time_str.startswith('сегодня'):
            h, mi = _extract_hhmm(time_str)
            if h is not None:
                result = current_time.replace(hour=h, minute=mi, second=0, microsecond=0)
                if result <= current_time:
                    result += timedelta(days=1)
                logger.info(f"✅ Simple fallback parsed '{time_str}' → {result}")
                return result
        
        # "утром/днём/вечером/ночью"
        time_of_day = {
            'утром': 9, 'утро': 9,
            'днём': 14, 'днем': 14,
            'вечером': 19, 'вечер': 19,
            'ночью': 23, 'ночь': 23,
            'в обед': 13, 'после обеда': 15,
        }
        for keyword, hour in time_of_day.items():
            if keyword in time_str:
                result = current_time.replace(hour=hour, minute=0, second=0, microsecond=0)
                if result <= current_time:
                    result += timedelta(days=1)
                logger.info(f"✅ Simple fallback parsed '{time_str}' → {result}")
                return result
        
        # Only HH:MM format
        if ':' in time_str:
            h, mi = _extract_hhmm(time_str)
            if h is not None:
                result = current_time.replace(hour=h, minute=mi, second=0, microsecond=0)
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
        
        # Try simple parsing (AI parsing is async and cannot be called from sync)
        return parse_time_simple_fallback(time_str, current_time)
        
    except Exception as e:
        logger.error(f"Error in parse_time: {e}")
        return None
