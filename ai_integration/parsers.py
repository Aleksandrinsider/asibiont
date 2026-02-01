import re
from datetime import datetime, timedelta

async def extract_task_details(message: str, user_id: int = None):
    """
    Extract task details from message.
    For PoC, simple regex parsing.
    TODO: Integrate with AI for better parsing.
    """
    message_lower = message.lower()

    # Time parsing first
    reminder_time = None

    # "в 7:00", "в 19:00"
    time_match = re.search(r'в\s+(\d{1,2}):(\d{2})', message_lower)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
        now = datetime.now()
        reminder_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if reminder_time < now:
            reminder_time = reminder_time.replace(day=now.day + 1)  # Tomorrow if time passed

    # "сегодня в 7:00"
    if 'сегодня' in message_lower and time_match:
        reminder_time = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)

    # "завтра в 7:00"
    if 'завтра' in message_lower and time_match:
        tomorrow = datetime.now() + timedelta(days=1)
        reminder_time = tomorrow.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # "вечером в 7:00" - assume evening
    if 'вечером' in message_lower and time_match:
        reminder_time = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)

    # "через 5 минут/часов/дней"
    time_match_relative = re.search(r'через (\d+) (минут|час|день|дня)', message_lower)
    if time_match_relative and not reminder_time:
        amount = int(time_match_relative.group(1))
        unit = time_match_relative.group(2)
        now = datetime.now()

        if 'минут' in unit:
            reminder_time = now + timedelta(minutes=amount)
        elif 'час' in unit:
            reminder_time = now + timedelta(hours=amount)
        elif 'день' in unit or 'дня' in unit:
            reminder_time = now + timedelta(days=amount)

    # Parse specific date and time: "01.02.2026 17:46"
    date_match = re.search(r'(\d{2})\.(\d{2})\.(\d{4})\s+(\d{1,2}):(\d{2})', message)
    if date_match and not reminder_time:
        day = int(date_match.group(1))
        month = int(date_match.group(2))
        year = int(date_match.group(3))
        hour = int(date_match.group(4))
        minute = int(date_match.group(5))
        reminder_time = datetime(year, month, day, hour, minute)

    # Parse delay: "с отставанием на 10 мин"
    delay = timedelta()
    delay_match = re.search(r'с отставанием на (\d+) (минут|час|мин)', message_lower)
    if delay_match:
        delay_amount = int(delay_match.group(1))
        delay_unit = delay_match.group(2)
        if 'минут' in delay_unit or 'мин' in delay_unit:
            delay = timedelta(minutes=delay_amount)
        elif 'час' in delay_unit:
            delay = timedelta(hours=delay_amount)

    if reminder_time and delay:
        reminder_time += delay

    # Title extraction - remove command words and time expressions
    title = message

    # Remove common command prefixes
    title = re.sub(r'^(напомни|создай|запланируй|добавь)\s+', '', title, flags=re.IGNORECASE)

    # Remove time expressions
    title = re.sub(r'\b(сегодня|завтра|вечером|утром|в|через|на|спустя|с|отставанием)\b', '', title, flags=re.IGNORECASE)

    # Remove time patterns
    title = re.sub(r'\d{1,2}:\d{2}', '', title)
    title = re.sub(r'\d+\s*(минут|час|день|дня|мин)', '', title)
    title = re.sub(r'\d{2}\.\d{2}\.\d{4}', '', title)

    # Clean up extra spaces
    title = re.sub(r'\s+', ' ', title).strip()

    # If title is empty or too short, use original message
    if len(title) < 3:
        title = message

    return {
        'title': title,
        'reminder_time': reminder_time,
        'description': ''
    }