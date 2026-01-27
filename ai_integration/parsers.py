import re
from datetime import datetime, timedelta

async def extract_task_details(message: str, user_id: int = None):
    """
    Extract task details from message.
    For PoC, simple regex parsing.
    TODO: Integrate with AI for better parsing.
    """
    # Simple title extraction
    title = message.strip()

    # Simple time parsing (e.g., "через 5 минут")
    time_match = re.search(r'через (\d+) (минут|час|день|дня)', message.lower())
    reminder_time = None
    if time_match:
        amount = int(time_match.group(1))
        unit = time_match.group(2)
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