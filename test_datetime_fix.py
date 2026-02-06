"""
Тест для проверки корректности определения времени агентом
"""
import asyncio
import os
os.environ['LOCAL'] = '1'

from datetime import datetime
import pytz
import logging

# Включаем логирование
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(name)s - %(message)s')

from models import Session, User, UserProfile
from ai_integration.chat import chat_with_ai

async def test_datetime():
    """Проверяем, что агент правильно определяет текущее время"""
    print("=" * 60)
    print("ТЕСТ ОПРЕДЕЛЕНИЯ ВРЕМЕНИ АГЕНТОМ")
    print("=" * 60)
    
    session = Session()
    
    try:
        # Создаем тестового пользователя
        test_user = User(
            telegram_id=999999999,
            username="datetime_test_user",
            timezone="Europe/Moscow"
        )
        session.add(test_user)
        session.commit()
        
        # Получаем текущее реальное время
        base_now = datetime.now(pytz.UTC)
        moscow_tz = pytz.timezone('Europe/Moscow')
        real_moscow_time = base_now.astimezone(moscow_tz)
        
        months = [
            'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
            'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'
        ]
        
        expected_time = f"{real_moscow_time.strftime('%H:%M')}"
        expected_date = f"{real_moscow_time.day} {months[real_moscow_time.month - 1]} {real_moscow_time.year}"
        
        print(f"\n[OK] Реальное время (Москва): {expected_time}")
        print(f"[OK] Реальная дата: {expected_date}")
        print()
        
        # Спрашиваем у агента про время
        result = await chat_with_ai(
            user_id=999999999,
            message="Сколько сейчас времени и какая дата?",
            message_type="normal"
        )
        
        # chat_with_ai возвращает dict с ключом 'response'
        response = result.get('response', '') if isinstance(result, dict) else result
        
        print(f"[AI] Ответ агента:")
        print(f"{response}")
        print()
        
        # Проверяем, что агент упомянул правильное время
        response_lower = response.lower()
        hour_str = str(real_moscow_time.hour)
        day_str = str(real_moscow_time.day)
        month_str = months[real_moscow_time.month - 1]
        year_str = str(real_moscow_time.year)
        
        hour_found = hour_str in response
        day_found = day_str in response
        month_found = month_str in response_lower
        year_found = year_str in response
        
        print("[CHECK] Проверка компонентов:")
        print(f"  Час ({hour_str}): {'[OK]' if hour_found else '[FAIL]'}")
        print(f"  День ({day_str}): {'[OK]' if day_found else '[FAIL]'}")
        print(f"  Месяц ({month_str}): {'[OK]' if month_found else '[FAIL]'}")
        print(f"  Год ({year_str}): {'[OK]' if year_found else '[FAIL]'}")
        print()
        
        if all([hour_found, day_found, month_found, year_found]):
            print("[SUCCESS] Агент правильно определил дату и время!")
        else:
            print("[ERROR] Агент неправильно определил дату/время!")
            print(f"   Ожидалось: {expected_time}, {expected_date}")
        
    finally:
        # Очищаем тестовые данные
        session.query(User).filter_by(telegram_id=999999999).delete()
        session.commit()
        session.close()

if __name__ == "__main__":
    asyncio.run(test_datetime())
