"""
Тестирование обработки времени агентом - проверка различных сценариев
"""
import os
import sys
import asyncio
from datetime import datetime, timedelta
import pytz

# Устанавливаем LOCAL=1 для использования SQLite
os.environ['LOCAL'] = '1'

# Настройка путей
sys.path.insert(0, os.path.dirname(__file__))

from models import init_db, User, UserProfile, Task, SessionLocal as ModelsSessionLocal
from ai_integration.chat import chat_with_ai

# Используем SessionLocal из models.py
SessionLocal = ModelsSessionLocal

async def setup_test_user():
    """Создать тестового пользователя"""
    init_db()
    
    session = SessionLocal()
    try:
        # Удаляем существующие записи (если есть)
        existing_user = session.query(User).filter(User.telegram_id == 999999).first()
        if existing_user:
            session.query(Task).filter(Task.user_id == existing_user.id).delete()
            session.query(UserProfile).filter(UserProfile.user_id == existing_user.id).delete()
            session.query(User).filter(User.id == existing_user.id).delete()
            session.commit()
        
        # Создаем нового
        user = User(
            telegram_id=999999,
            username="test_time_user",
            timezone="Europe/Moscow",
            subscription_tier="GOLD"
        )
        session.add(user)
        session.commit()
        
        profile = UserProfile(
            user_id=user.id,
            interests="тестирование, программирование",
            bio="Тестовый пользователь для проверки обработки времени"
        )
        session.add(profile)
        session.commit()
        
        print(f"✅ Создан тестовый пользователь: {user.username} (ID: {user.id})")
        return user.id
    finally:
        session.close()

async def test_scenario(user_id: int, scenario_name: str, messages: list):
    """Тестировать один сценарий"""
    print(f"\n{'='*60}")
    print(f"🧪 СЦЕНАРИЙ: {scenario_name}")
    print(f"{'='*60}")
    
    session = SessionLocal()
    try:
        for i, msg in enumerate(messages, 1):
            print(f"\n👤 Пользователь ({i}/{len(messages)}): {msg}")
            
            response = await chat_with_ai(
                message=msg,
                user_id=user_id,
                db_session=session
            )
            
            print(f"🤖 ASI Biont: {response[:500]}{'...' if len(response) > 500 else ''}")
            
            # Проверяем задачи после каждого сообщения
            tasks = session.query(Task).filter(
                Task.user_id == user_id,
                Task.status != 'completed'
            ).all()
            
            if tasks:
                print(f"\n📋 Текущие задачи ({len(tasks)}):")
                for task in tasks:
                    print(f"  • {task.title}")
                    print(f"    Напоминание: {task.reminder_time}")
                    if task.description:
                        print(f"    Описание: {task.description}")
            
            # Небольшая пауза между сообщениями
            await asyncio.sleep(0.5)
            
    finally:
        session.close()

async def run_tests():
    """Запустить все тесты"""
    print("\n" + "="*60)
    print("🚀 НАЧАЛО ТЕСТИРОВАНИЯ ОБРАБОТКИ ВРЕМЕНИ")
    print("="*60)
    
    user_id = await setup_test_user()
    
    # Текущее время для тестов
    moscow_tz = pytz.timezone('Europe/Moscow')
    now = datetime.now(moscow_tz)
    current_time = now.strftime('%H:%M')
    current_date = now.strftime('%d.%m.%Y')
    
    print(f"\n⏰ Текущее время: {current_time} {current_date}")
    
    # Сценарий 1: Относительное время "через X минут" - должен СПРОСИТЬ время
    await test_scenario(
        user_id, 
        "Относительное время 'через 15 минут' - должен спросить точное время",
        [
            "напомни через 15 минут позвонить маме"
        ]
    )
    
    # Сценарий 2: Точное время - должен СРАЗУ создать задачу
    future_time = (now + timedelta(minutes=30)).strftime('%H:%M')
    await test_scenario(
        user_id,
        f"Точное время '{future_time}' - должен сразу создать задачу БЕЗ вопросов",
        [
            f"напомни в {future_time} купить продукты"
        ]
    )
    
    # Сценарий 3: Относительное "завтра" - должен СПРОСИТЬ точное время
    await test_scenario(
        user_id,
        "Относительное 'завтра' - должен спросить точное время",
        [
            "напомни завтра встретиться с другом"
        ]
    )
    
    # Сценарий 4: "Завтра" + точное время - должен создать задачу
    await test_scenario(
        user_id,
        "Завтра + точное время - должен создать задачу с правильной датой",
        [
            "напомни завтра в 10:00 сходить к врачу"
        ]
    )
    
    # Сценарий 5: Диалог с уточнением времени
    await test_scenario(
        user_id,
        "Диалог: сначала относительное время, потом точное",
        [
            "напомни вечером пойти на тренировку",
            "в 19:00"
        ]
    )
    
    # Сценарий 6: "Через час" - должен спросить точное время
    await test_scenario(
        user_id,
        "Относительное 'через час' - должен спросить точное время",
        [
            "напомни через час проверить почту"
        ]
    )
    
    # Сценарий 7: Время в прошлом - должен понять что это СЕГОДНЯ если еще не наступило
    if int(now.strftime('%H')) < 20:
        await test_scenario(
            user_id,
            "Время позже текущего - должен установить на СЕГОДНЯ",
            [
                "напомни в 20:00 посмотреть сериал"
            ]
        )
    
    # Сценарий 8: Неоднозначное "утром/днем/вечером" - должен спросить точное время
    await test_scenario(
        user_id,
        "Неоднозначное 'утром' - должен спросить точное время",
        [
            "напомни завтра утром сделать зарядку"
        ]
    )
    
    print("\n" + "="*60)
    print("✅ ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")
    print("="*60)
    
    # Итоговая статистика
    session = SessionLocal()
    try:
        tasks = session.query(Task).filter(
            Task.user_id == user_id,
            Task.is_deleted == False
        ).all()
        
        print(f"\n📊 ИТОГО создано задач: {len(tasks)}")
        print("\n📋 Все задачи:")
        for i, task in enumerate(tasks, 1):
            print(f"\n{i}. {task.title}")
            print(f"   Напоминание: {task.reminder_time}")
            if task.description:
                print(f"   Описание: {task.description}")
            print(f"   Создана: {task.created_at}")
        
        # Проверка ошибок
        print("\n" + "="*60)
        print("🔍 АНАЛИЗ ОШИБОК:")
        print("="*60)
        
        errors = []
        for task in tasks:
            if task.reminder_time:
                task_date = task.reminder_time.date()
                today = now.date()
                tomorrow = today + timedelta(days=1)
                
                # Проверка на неправильную дату (не сегодня и не завтра)
                if task_date != today and task_date != tomorrow:
                    errors.append(f"❌ ОШИБКА: Задача '{task.title}' имеет неправильную дату: {task.reminder_time}")
                
                # Проверка времени в прошлом
                if task.reminder_time < now:
                    errors.append(f"❌ ОШИБКА: Задача '{task.title}' имеет время в ПРОШЛОМ: {task.reminder_time}")
        
        if errors:
            print("\n".join(errors))
        else:
            print("✅ Все задачи имеют корректные даты и время!")
            
    finally:
        session.close()
    
    print("\n" + "="*60)
    print("ОЖИДАЕМОЕ ПОВЕДЕНИЕ:")
    print("="*60)
    print("1. ✅ Относительное время → агент ДОЛЖЕН СПРОСИТЬ точное время")
    print("2. ✅ Точное время → агент создает задачу БЕЗ вопросов")
    print("3. ✅ Неоднозначное время → агент ДОЛЖЕН УТОЧНИТЬ")
    print("4. ✅ Все даты должны быть КОРРЕКТНЫМИ (сегодня или завтра)")
    print("5. ✅ Время НЕ должно быть в прошлом")
    print("="*60)

if __name__ == "__main__":
    asyncio.run(run_tests())
