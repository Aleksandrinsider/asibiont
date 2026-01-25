#!/usr/bin/env python3
"""
Тест для проверки парсинга времени
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.utils import parse_time_to_datetime

def test_time_parsing():
    """Тест парсинга времени"""
    print("🧪 Тестирование парсинга времени")

    # Тестовый user_id (нужен для timezone)
    user_id = 123456789

    # Проверим текущий день недели
    from datetime import datetime
    import pytz
    from models import Session, User

    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    user_tz = pytz.timezone(user.timezone) if user and user.timezone else pytz.UTC
    session.close()
    now = datetime.now(user_tz)
    print(f"Сегодня: {now.strftime('%Y-%m-%d %A')} (weekday: {now.weekday()})")

    test_cases = [
        ("вторник в 10:00", "2026-01-27 10:00"),  # Сегодня 25.01.2026 (воскресенье)
        ("среда в 15:30", "2026-01-28 15:30"),
        ("пятница", "2026-01-30 09:00"),  # Без времени - ставим 9:00
        ("завтра в 14:00", "2026-01-26 14:00"),
        ("через 2 часа", None),  # Относительное время
    ]

    for input_text, expected in test_cases:
        print(f"\n--- Тестируем: '{input_text}' ---")
        result = parse_time_to_datetime(input_text, user_id)
        print(f"Результат: {result} (Ожидалось: {expected})")
        
        # Отладка регулярного выражения
        import re
        weekdays = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье']
        weekday_match = re.search(r"(" + "|".join(weekdays) + r")(?:\s+(?:в\s+)?(\d{1,2}):(\d{2}))?", input_text.lower())
        if weekday_match:
            print(f"Regex match: {weekday_match.groups()}")
        else:
            print("Regex не нашел совпадение")
        
        if expected and result != expected:
            print(f"  ❌ НЕ СОВПАДАЕТ!")
        elif not expected and result is None:
            print(f"  ✅ Правильно не распарсилось")
        elif expected and result == expected:
            print(f"  ✅ СОВПАДАЕТ!")

if __name__ == "__main__":
    test_time_parsing()