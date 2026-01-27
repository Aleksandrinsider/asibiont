import re
from datetime import datetime, timedelta

async def extract_task_details(message: str, user_id: int = None):
    """
    Extract task details from message.
    For PoC, simple regex parsing.
    TODO: Integrate with AI for better parsing.
    """
    # Simple title extraction - remove time-related words
    title = re.sub(r'\b(сегодня|завтра|вечером|утром|в|через|на)\b', '', message, flags=re.IGNORECASE).strip()
    title = re.sub(r'\d{1,2}:\d{2}', '', title).strip()  # Remove time like 7:00
    title = re.sub(r'\d{1,2}\s*час', '', title).strip()  # Remove "7 час"

    # Time parsing
    reminder_time = None
    message_lower = message.lower()

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

    return {
        'title': title,
        'reminder_time': reminder_time,
        'description': ''
    }