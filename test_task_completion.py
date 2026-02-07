"""
Тест закрытия задач через AI при различных подтверждающих фразах
"""
import asyncio
import sys
import os
from datetime import datetime, timedelta, timezone

# Добавляем путь к корневой директории
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import Session, User, UserProfile, Task, Base, engine
from config import DATABASE_URL

async def test_task_completion():
    """Тестируем различные варианты подтверждения выполнения задачи"""
    
    print("\n" + "="*60)
    print("ТЕСТ: Закрытие задачи через AI")
    print("="*60 + "\n")
    
    # Создаем базу данных
    Base.metadata.create_all(engine)
    session = Session()
    
    # Тестовый пользователь
    test_user_id = 999888777
    user = session.query(User).filter_by(telegram_id=test_user_id).first()
    
    if not user:
        print("Создаем тестового пользователя...")
        user = User(
            telegram_id=test_user_id,
            username='test_user',
            first_name='Test',
            timezone='Europe/Moscow'
        )
        session.add(user)
        session.commit()
        
        profile = UserProfile(
            user_id=user.id,
            interests='тестирование, программирование',
            goals='проверить функционал бота',
            city='Москва'
        )
        session.add(profile)
        session.commit()
        print("✅ Пользователь создан\n")
    else:
        print("✅ Используем существующего пользователя\n")
    
    # Удаляем старые задачи
    session.query(Task).filter_by(user_id=user.id).delete()
    session.commit()
    
    # Создаем тестовую задачу
    reminder_time = datetime.now(timezone.utc) - timedelta(minutes=10)  # Просроченная на 10 минут
    
    test_task = Task(
        user_id=user.id,
        title="Проверить электронную почту",
        description="Проверить важные письма",
        status='pending',
        reminder_time=reminder_time,
        reminder_sent=True
    )
    session.add(test_task)
    session.commit()
    task_id = test_task.id
    
    print(f"📝 Создана тестовая задача:")
    print(f"   ID: {task_id}")
    print(f"   Название: {test_task.title}")
    print(f"   Статус: {test_task.status}")
    print(f"   Напоминание: {reminder_time.strftime('%Y-%m-%d %H:%M')}\n")
    
    # Устанавливаем current_task_id для контекста
    user.current_task_id = task_id
    session.commit()
    
    # Тестовые фразы
    test_phrases = [
        "я уже проверил почту",
        "проверил",
        "сделал",
        "готово",
        "выполнил задачу",
        "закончил с этим",
        "её закрыл",
        "уже сделано"
    ]
    
    print("\n" + "="*60)
    print("ТЕСТИРУЕМЫЕ ФРАЗЫ:")
    print("="*60)
    for i, phrase in enumerate(test_phrases, 1):
        print(f"{i}. '{phrase}'")
    print()
    
    # Тестируем все фразы
    print("\n" + "="*60)
    print("ТЕСТИРОВАНИЕ ВСЕХ ФРАЗ:")
    print("="*60 + "\n")
    
    for i, test_phrase in enumerate(test_phrases, 1):
        # Создаем новую задачу для каждой фразы
        test_task = Task(
            user_id=user.id,
            title="Проверить электронную почту",
            description="Проверить важные письма",
            status='pending',
            reminder_time=reminder_time,
            reminder_sent=True
        )
        session.add(test_task)
        session.commit()
        task_id = test_task.id
        
        # Устанавливаем current_task_id
        user.current_task_id = task_id
        session.commit()
        
        print("-"*60)
        print(f"ТЕСТ {i}/{len(test_phrases)}: '{test_phrase}'")
        print("-"*60)
        
        try:
            # Вызываем AI
            response = await chat_with_ai(
                test_phrase,
                user_id=test_user_id,
                db_session=session
            )
            
            # Проверяем результат
            tool_calls = response.get('tool_calls', [])
            
            # Обновляем данные задачи
            session.expire_all()
            updated_task = session.query(Task).filter_by(id=task_id).first()
            
            if updated_task and updated_task.status == 'completed':
                print(f"✅ УСПЕХ: Задача закрыта")
            else:
                print(f"❌ ПРОВАЛ: Задача не закрыта (статус: {updated_task.status if updated_task else 'не найдена'})")
            
        except Exception as e:
            print(f"❌ ОШИБКА: {type(e).__name__}: {str(e)}")
        
        # Удаляем тестовую задачу
        session.query(Task).filter_by(id=task_id).delete()
        user.current_task_id = None
        session.commit()
        
        print()
    
    print("="*60)
    print("ВСЕ ТЕСТЫ ЗАВЕРШЕНЫ")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(test_task_completion())
