"""
Комплексное тестирование AI-агента:
- Управление задачами (добавление, удаление, завершение, изменение)
- Заполнение профиля
- Поиск контактов
- Напоминания
- Проверка отображения в панели
"""

import asyncio
import sys
from ai_integration import chat_with_ai
from models import Session, Task, User, UserProfile, Interaction
from datetime import datetime, timedelta
import pytz

sys.stdout.reconfigure(encoding='utf-8')

user_id = 146333757

def print_section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")

def check_database():
    """Проверка состояния БД"""
    db = Session()
    try:
        user = db.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            print("❌ Пользователь не найден")
            return
        
        tasks = db.query(Task).filter_by(user_id=user.id).all()
        profile = db.query(UserProfile).filter_by(user_id=user.id).first()
        
        print(f"📋 ЗАДАЧИ ({len(tasks)}):")
        for task in tasks:
            status_icon = "✅" if task.status == "completed" else "⏳"
            print(f"  {status_icon} {task.title} (статус: {task.status}, напоминание: {task.reminder_time})")
        
        print(f"\n👤 ПРОФИЛЬ:")
        if profile:
            print(f"  Город: {profile.city or 'не указан'}")
            print(f"  Интересы: {profile.interests or 'не указаны'}")
            print(f"  Навыки: {profile.skills or 'не указаны'}")
            print(f"  Цели: {profile.goals or 'не указаны'}")
        else:
            print("  ❌ Профиль не создан")
            
    finally:
        db.close()

async def test_scenario(message, description):
    """Выполнить тестовый сценарий"""
    print(f"👤 {description}")
    print(f"   Сообщение: {message}")
    response = await chat_with_ai(message, [], user_id)
    print(f"🤖 Ответ: {response[:200]}{'...' if len(response) > 200 else ''}\n")
    await asyncio.sleep(2)  # Задержка между запросами

async def run_tests():
    print_section("КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ AI-АГЕНТА")
    
    # 1. УПРАВЛЕНИЕ ЗАДАЧАМИ
    print_section("1. УПРАВЛЕНИЕ ЗАДАЧАМИ")
    
    print("🔹 Тест: Добавление задачи с явным временем")
    await test_scenario(
        "Добавь задачу позвонить клиенту завтра в 14:00",
        "Добавление задачи с конкретным временем"
    )
    check_database()
    
    print("🔹 Тест: Добавление задачи с относительным временем")
    await test_scenario(
        "Напомни купить хлеб через 2 часа",
        "Задача с относительным временем"
    )
    check_database()
    
    print("🔹 Тест: Просмотр задач")
    await test_scenario(
        "Покажи мои задачи",
        "Просмотр всех задач"
    )
    
    print("🔹 Тест: Завершение задачи")
    await test_scenario(
        "Я купил хлеб, отметь как выполненное",
        "Завершение задачи"
    )
    check_database()
    
    print("🔹 Тест: Изменение времени задачи")
    await test_scenario(
        "Перенеси звонок клиенту на 30 минут позже",
        "Изменение времени напоминания"
    )
    check_database()
    
    print("🔹 Тест: Удаление задачи")
    await test_scenario(
        "Удали задачу позвонить клиенту",
        "Удаление конкретной задачи"
    )
    check_database()
    
    # 2. РАБОТА С ПРОФИЛЕМ
    print_section("2. РАБОТА С ПРОФИЛЕМ")
    
    print("🔹 Тест: Обновление интересов")
    await test_scenario(
        "Я интересуюсь блокчейном и криптовалютами",
        "Добавление интересов"
    )
    check_database()
    
    print("🔹 Тест: Указание города")
    await test_scenario(
        "Живу в Санкт-Петербурге",
        "Обновление города"
    )
    check_database()
    
    print("🔹 Тест: Указание навыков")
    await test_scenario(
        "Мои навыки: JavaScript, React, Node.js",
        "Обновление навыков"
    )
    check_database()
    
    # 3. ПОИСК КОНТАКТОВ
    print_section("3. ПОИСК КОНТАКТОВ")
    
    print("🔹 Тест: Поиск людей")
    await test_scenario(
        "Найди людей с похожими интересами",
        "Поиск контактов по интересам"
    )
    
    # 4. ПРОВЕРКА ЛОГИКИ
    print_section("4. ПРОВЕРКА ЛОГИКИ И ПОВЕДЕНИЯ")
    
    print("🔹 Тест: Уточнение времени при добавлении")
    await test_scenario(
        "Добавь задачу сделать презентацию",
        "AI должен спросить когда напомнить"
    )
    
    print("🔹 Тест: Быстрое подтверждение удаления")
    await test_scenario(
        "Удали все задачи",
        "AI должен сразу удалить без лишних вопросов"
    )
    check_database()
    
    # ИТОГИ
    print_section("ИТОГОВОЕ СОСТОЯНИЕ")
    check_database()
    
    print_section("ТЕСТ ЗАВЕРШЕН")
    print("✅ Все сценарии выполнены")
    print("📊 Проверьте дашборд: http://localhost:8080/dashboard")

if __name__ == "__main__":
    asyncio.run(run_tests())
