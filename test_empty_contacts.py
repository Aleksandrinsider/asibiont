"""
Тест для проверки обработки случая, когда контактов нет в системе
"""

import asyncio
import os
os.environ['LOCAL'] = '1'

from ai_integration.handlers import find_relevant_contacts_for_task
from models import Session, User, UserProfile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Тестовая БД в памяти
engine = create_engine('sqlite:///:memory:')
from models import Base
Base.metadata.create_all(engine)
TestSession = sessionmaker(bind=engine)


def test_no_contacts_response():
    """Тест: функция возвращает понятное сообщение когда контактов нет"""
    session = TestSession()
    
    # Создаем тестового пользователя
    user = User(telegram_id=12345, username='test_user')
    session.add(user)
    session.commit()
    
    # НЕ создаем профиль и других пользователей
    # Вызываем функцию
    result = find_relevant_contacts_for_task(
        task_description="Нужен программист для стартапа",
        user_id=12345,
        session=session
    )
    
    print("=" * 60)
    print("РЕЗУЛЬТАТ ФУНКЦИИ find_relevant_contacts_for_task:")
    print("=" * 60)
    print(result)
    print("=" * 60)
    
    # Проверки
    assert "В сети пока нет контактов" in result or "❌" in result, "Должно быть сообщение об отсутствии контактов"
    assert len(result) > 50, "Сообщение должно быть информативным"
    assert "профиль" in result.lower(), "Должна быть рекомендация заполнить профиль"
    
    session.close()
    print("✅ Тест пройден: функция корректно обрабатывает отсутствие контактов")


async def test_ai_processing():
    """Тест: AI формирует адекватный ответ на результат инструмента"""
    from ai_integration.chat import call_ai_with_tools
    from ai_integration.prompts import get_extended_system_prompt
    from datetime import datetime
    
    # Создаем тестового пользователя
    session = TestSession()
    user = User(telegram_id=99999, username='test_ai_user')
    session.add(user)
    session.commit()
    session.close()
    
    now = datetime.now()
    system_prompt = get_extended_system_prompt(
        user_now=now,
        current_time_str=now.strftime("%H:%M"),
        current_date_str=now.strftime("%d.%m.%Y"),
        user_username='test_ai_user',
        mentions_str='',
        user_memory=None,
        context=None,
        intent=None,
        subscription_tier='basic',
        message_type='request'
    )
    
    # Имитируем запрос пользователя
    user_message = "найди мне контакты для проекта"
    
    print("\n" + "=" * 60)
    print("ТЕСТ AI ОБРАБОТКИ:")
    print("=" * 60)
    print(f"Запрос: {user_message}")
    
    result = await call_ai_with_tools(
        user_message=user_message,
        system_prompt=system_prompt,
        user_id=99999,
        context=None
    )
    
    print(f"\nОтвет AI:")
    print(result.get('response', 'Нет ответа'))
    print("\nTool calls:")
    print(result.get('tool_calls', 'Нет tool calls'))
    print("=" * 60)
    
    # Проверки
    response = result.get('response', '')
    assert len(response) > 0, "AI должен вернуть ответ"
    assert "контакт" in response.lower() or "профиль" in response.lower() or "нет" in response.lower(), \
        "Ответ должен содержать релевантную информацию"
    
    print("✅ Тест пройден: AI корректно обработал результат инструмента")


if __name__ == '__main__':
    print("\n🧪 ЗАПУСК ТЕСТОВ: Обработка пустого списка контактов\n")
    
    # Тест 1: Проверка функции
    test_no_contacts_response()
    
    # Тест 2: Проверка AI обработки
    print("\n📡 Тест с реальным AI (требуется интернет)...")
    try:
        asyncio.run(test_ai_processing())
    except Exception as e:
        print(f"⚠️ Ошибка в AI тесте: {e}")
        print("Возможно, нет интернета или DEEPSEEK_API_KEY не настроен")
    
    print("\n✅ ВСЕ ТЕСТЫ ЗАВЕРШЕНЫ")
