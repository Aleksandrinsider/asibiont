#!/usr/bin/env python3
"""
Специфический тест для проверки дубликатов задач
"""
import asyncio
import os
import sys
from ai_integration.chat import chat_with_ai

# Настройки для локального тестирования
os.environ["LOCAL"] = "1"
os.environ["FREE_ACCESS_MODE"] = "1"

# Принудительное обновление конфигурации
import importlib
import config
importlib.reload(config)

async def test_duplicate_prevention():
    """Тестируем предотвращение дубликатов задач"""
    print("🧪 ТЕСТИРУЕМ ПРЕДОТВРАЩЕНИЕ ДУБЛИКАТОВ ЗАДАЧ")
    print("=" * 60)
    
    # Создаем пользователя заранее
    from models import User, SessionLocal
    
    test_user_id = 12345
    session = SessionLocal()
    
    # Проверяем или создаем пользователя
    user = session.query(User).filter_by(telegram_id=test_user_id).first()
    if not user:
        user = User(telegram_id=test_user_id, username="test_user")
        session.add(user)
        session.commit()
        print(f"Создан тестовый пользователь: {test_user_id}")
    else:
        print(f"Используем существующего пользователя: {test_user_id}")
    
    session.close()
    
    # Тест 1: Создать задачу с разным регистром
    print("\n📝 ТЕСТ 1: Создание задач с разным регистром")
    print("-" * 40)
    
    message1 = "заказать продукты через 5 минут"
    print(f"Сообщение 1: '{message1}'")
    response1 = await chat_with_ai(message1, user_id=test_user_id)
    print(f"Ответ 1: {response1[:100]}...")
    
    # Небольшая пауза
    await asyncio.sleep(1)
    
    message2 = "Заказать продукты через 3 минуты"  # Заглавная буква 
    print(f"\nСообщение 2: '{message2}'")
    response2 = await chat_with_ai(message2, user_id=test_user_id)
    print(f"Ответ 2: {response2[:100]}...")
    
    # Проверяем список задач
    print("\n📋 Проверяем итоговый список задач:")
    response_list = await chat_with_ai("покажи мои задачи", user_id=test_user_id)
    print(f"Список задач: {response_list}")
    
    print("\n" + "=" * 60)
    print("✅ ТЕСТ ЗАВЕРШЕН")

if __name__ == "__main__":
    asyncio.run(test_duplicate_prevention())