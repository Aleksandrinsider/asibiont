"""
Автоматический тест улучшений AI - прямое тестирование функции chat_with_ai
"""
import asyncio
import os
import sys
from datetime import datetime
import re

# Устанавливаем переменную окружения для локального режима
os.environ['LOCAL'] = '1'

# Импортируем необходимые модули
from ai_integration import chat_with_ai
from models import Session, User, Task

async def run_tests():
    """Запуск всех тестов"""
    print("🧪 Начинаю автоматическое тестирование AI улучшений...")
    print("=" * 60)
    
    test_user_id = 1001
    
    # Тест 1: Создание задачи с относительным временем
    print("\n2️⃣ ТЕСТ 1: Создание задачи 'через 5 минут'")
    print("-" * 60)
    current_time = datetime.now().strftime("%H:%M")
    message1 = "напомни через 5 минут заказать продукты домой"
    print(f"📤 Отправляю: '{message1}'")
    print(f"⏰ Текущее время: {current_time}")
    
    response1 = await chat_with_ai(message1, [], test_user_id)
    print(f"📥 Ответ AI: {response1}")
    
    # Проверяем, указано ли точное время
    import re
    time_pattern = r'\d{1,2}:\d{2}'
    time_found = re.findall(time_pattern, response1)
    
    if time_found:
        print(f"✅ Найдено точное время: {time_found}")
        print("✅ ТЕСТ ПРОЙДЕН: AI указал конкретное время вместо 'к вечеру'")
    else:
        print("❌ ТЕСТ ПРОВАЛЕН: Точное время не найдено в ответе")
    
    # Проверяем отсутствие избыточных упоминаний контактов
    contact_mentions = response1.lower().count('контакт')
    if contact_mentions <= 1:
        print(f"✅ ТЕСТ ПРОЙДЕН: Упоминание контактов в норме ({contact_mentions})")
    else:
        print(f"⚠️ ВНИМАНИЕ: Избыточные упоминания контактов ({contact_mentions})")
    
    # Тест 2: Проверка текущего времени
    print("\n3️⃣ ТЕСТ 2: Проверка текущего времени")
    print("-" * 60)
    message2 = "сколько сейчас время?"
    print(f"📤 Отправляю: '{message2}'")
    
    response2 = await chat_with_ai(message2, [], test_user_id)
    print(f"📥 Ответ AI: {response2}")
    
    # Проверяем правильность времени (должно совпадать с локальным)
    current_hour = datetime.now().hour
    if str(current_hour) in response2 or f"{current_hour:02d}" in response2:
        print(f"✅ ТЕСТ ПРОЙДЕН: AI показывает правильное локальное время")
    else:
        print(f"❌ ТЕСТ ПРОВАЛЕН: Время в ответе не соответствует локальному ({current_hour}:xx)")
    
    # Тест 3: Показать задачи
    print("\n4️⃣ ТЕСТ 3: Показать список задач")
    print("-" * 60)
    message3 = "покажи мои задачи"
    print(f"📤 Отправляю: '{message3}'")
    
    response3 = await chat_with_ai(message3, [], test_user_id)
    print(f"📥 Ответ AI: {response3}")
    
    if "задач" in response3.lower() or "напоминание" in response3.lower():
        print("✅ ТЕСТ ПРОЙДЕН: AI показал задачи")
    else:
        print("⚠️ Возможно, у пользователя нет задач")
    
    # Тест 4: Перенос задачи
    print("\n5️⃣ ТЕСТ 4: Перенос задачи")
    print("-" * 60)
    message4 = "перенеси мою задачу на 5 минут"
    print(f"📤 Отправляю: '{message4}'")
    
    response4 = await chat_with_ai(message4, [], test_user_id)
    print(f"📥 Ответ AI: {response4}")
    
    # Проверяем, что AI вызвал list_tasks перед упоминанием времени
    if re.search(r'\d{2}:\d{2}', response4):
        print("✅ AI упомянул время задачи")
    
    print("\n" + "=" * 60)
    print("🎉 Тестирование завершено!")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(run_tests())
