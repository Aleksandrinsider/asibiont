"""
Тест для проверки отображения данных на dashboard в реальном времени
Использует основной аккаунт пользователя для мониторинга изменений
"""
import asyncio
import sys
from ai_integration import chat_with_ai
from models import Session, Task, User, UserProfile
from datetime import datetime, timedelta
import pytz

sys.stdout.reconfigure(encoding='utf-8')

USER_ID = 146333757  # aleksandrinsider

async def test_scenario(scenario_name, user_message, wait_time=5):
    """Выполнить один тестовый сценарий"""
    print(f"\n{'='*60}")
    print(f"📋 СЦЕНАРИЙ: {scenario_name}")
    print(f"{'='*60}")
    print(f"👤 Пользователь: {user_message}")
    
    try:
        # Загрузить контекст из БД
        context = []
        
        # Отправить сообщение
        response = await chat_with_ai(user_message, context, USER_ID)
        print(f"🤖 Агент: {response}")
        
        # Показать текущее состояние БД
        print(f"\n📊 ТЕКУЩЕЕ СОСТОЯНИЕ:")
        show_database_state()
        
        # Пауза для просмотра на dashboard
        print(f"\n⏳ Проверьте dashboard... (ожидание {wait_time} сек)")
        await asyncio.sleep(wait_time)
        
        return response
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return None

def show_database_state():
    """Показать текущее состояние БД"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=USER_ID).first()
        if not user:
            print("❌ Пользователь не найден")
            return
        
        # Задачи
        tasks = session.query(Task).filter_by(user_id=user.id).all()
        print(f"\n📋 Задачи ({len(tasks)}):")
        for task in tasks:
            status_emoji = "✅" if task.status == "completed" else "⏳"
            reminder = task.reminder_time.strftime("%d.%m %H:%M") if task.reminder_time else "нет"
            print(f"  {status_emoji} {task.title} (статус: {task.status}, напоминание: {reminder})")
        
        # Профиль
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            print(f"\n👤 Профиль:")
            print(f"  Город: {profile.city or 'не указан'}")
            print(f"  Интересы: {profile.interests or 'не указаны'}")
            print(f"  Навыки: {profile.skills or 'не указаны'}")
            print(f"  Цели: {profile.goals or 'не указаны'}")
        
    finally:
        session.close()

async def main():
    print("""
╔══════════════════════════════════════════════════════════╗
║        LIVE ТЕСТ - МОНИТОРИНГ DASHBOARD                  ║
╚══════════════════════════════════════════════════════════╝

Откройте dashboard в браузере: http://localhost:8080
User: aleksandrinsider
    """)
    
    input("Нажмите Enter когда будете готовы начать тестирование...")
    
    # Сценарий 1: Приветствие
    await test_scenario(
        "Приветствие",
        "Привет!",
        wait_time=5
    )
    
    # Сценарий 2: Добавление задачи с конкретным временем
    await test_scenario(
        "Добавление задачи",
        "Добавь задачу: Встреча с командой завтра в 14:00",
        wait_time=8
    )
    
    # Сценарий 3: Просмотр задач
    await test_scenario(
        "Просмотр задач",
        "Покажи мои задачи",
        wait_time=5
    )
    
    # Сценарий 4: Добавление задачи с относительным временем
    await test_scenario(
        "Задача с относительным временем",
        "Напомни позвонить клиенту через 2 часа",
        wait_time=8
    )
    
    # Сценарий 5: Обновление профиля
    await test_scenario(
        "Обновление интересов",
        "Я увлекаюсь блокчейном и Web3 технологиями",
        wait_time=8
    )
    
    # Сценарий 6: Завершение задачи
    await test_scenario(
        "Завершение задачи",
        "Я встретился с командой, отметь задачу выполненной",
        wait_time=8
    )
    
    # Сценарий 7: Удаление задачи
    await test_scenario(
        "Удаление задачи",
        "Удали задачу про звонок клиенту",
        wait_time=8
    )
    
    # Сценарий 8: Поиск контактов
    await test_scenario(
        "Поиск контактов",
        "Найди людей с похожими интересами",
        wait_time=8
    )
    
    print(f"\n{'='*60}")
    print("✅ ВСЕ СЦЕНАРИИ ВЫПОЛНЕНЫ")
    print(f"{'='*60}")
    print("\nПроверьте финальное состояние на dashboard")
    
    # Финальное состояние
    show_database_state()

if __name__ == "__main__":
    asyncio.run(main())
