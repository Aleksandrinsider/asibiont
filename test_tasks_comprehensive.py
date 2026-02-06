"""
Комплексный тест всех операций с задачами
"""
import asyncio
import sys
import os
from datetime import datetime, timedelta

# КРИТИЧЕСКИ ВАЖНО: Устанавливаем LOCAL=1 для использования SQLite
os.environ['LOCAL'] = '1'

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import Session, User, UserProfile, Task, Base, engine
from config import DATABASE_URL

# Цвета для вывода
GREEN = ''
RED = ''
YELLOW = ''
RESET = ''

def print_success(msg):
    print(f"[OK] {msg}")

def print_error(msg):
    print(f"[FAIL] {msg}")

def print_info(msg):
    print(f"[INFO] {msg}")

async def test_task_operations():
    """Тест всех операций с задачами"""
    
    user_id = 987654321
    print_info("Инициализация тестового пользователя...")
    
    # Создаем базу и пользователя
    Base.metadata.create_all(engine)
    session = Session()
    
    # Очищаем старые данные тестового пользователя
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if user:
        session.query(Task).filter_by(user_id=user.id).delete()
        session.commit()
    else:
        user = User(
            telegram_id=user_id, 
            username='test_tasks', 
            first_name='TaskTest',
            timezone='Europe/Moscow'
        )
        session.add(user)
        session.commit()
        
        profile = UserProfile(
            user_id=user.id,
            city='Москва',
            goals='Тестирование системы задач'
        )
        session.add(profile)
        session.commit()
    
    session.close()
    print_success("Пользователь готов")
    
    # ===== ТЕСТ 1: Создание задачи с напоминанием =====
    print_info("\n[ТЕСТ 1] Создание задачи с напоминанием...")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime('%d.%m')
    
    session = Session()
    response = await chat_with_ai(
        f'Создай задачу "Позвонить клиенту" на завтра {tomorrow} в 14:00',
        user_id=user_id,
        db_session=session
    )
    session.close()
    
    print(f"Ответ AI: {response.get('response', '')[:200]}")
    
    # Проверяем, создалась ли задача
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    tasks = session.query(Task).filter_by(user_id=user.id).all()
    session.close()
    
    if len(tasks) > 0:
        task = tasks[0]
        print_success(f"Задача создана: '{task.title}'")
        print_info(f"  - ID: {task.id}")
        print_info(f"  - Срок: {task.due_date}")
        print_info(f"  - Напоминание: {task.reminder_time}")
        
        if task.reminder_time:
            print_success("Напоминание установлено")
        else:
            print_error("Напоминание НЕ установлено!")
    else:
        print_error("Задача НЕ создана!")
        return
    
    task_id = task.id
    original_reminder = task.reminder_time
    
    # ===== ТЕСТ 2: Перенос задачи =====
    print_info("\n[ТЕСТ 2] Перенос задачи на другое время...")
    day_after_tomorrow = (datetime.now() + timedelta(days=2)).strftime('%d.%m')
    
    session = Session()
    response = await chat_with_ai(
        f'Перенеси задачу "Позвонить клиенту" на {day_after_tomorrow} в 16:00',
        user_id=user_id,
        db_session=session
    )
    session.close()
    
    print(f"Ответ AI: {response.get('response', '')[:200]}")
    
    # Проверяем изменения
    session = Session()
    updated_task = session.query(Task).filter_by(id=task_id).first()
    session.close()
    
    if updated_task:
        print_success(f"Задача найдена после переноса")
        print_info(f"  - Новый срок: {updated_task.due_date}")
        print_info(f"  - Новое напоминание: {updated_task.reminder_time}")
        
        if updated_task.due_date != task.due_date:
            print_success("Срок изменен")
        else:
            print_error("Срок НЕ изменен!")
        
        if updated_task.reminder_time and updated_task.reminder_time != original_reminder:
            print_success("Напоминание перенесено вместе с задачей")
        elif updated_task.reminder_time is None:
            print_error("Напоминание удалено при переносе!")
        else:
            print_error("Напоминание НЕ обновлено!")
    else:
        print_error("Задача не найдена после переноса!")
        return
    
    # ===== ТЕСТ 3: Редактирование задачи =====
    print_info("\n[ТЕСТ 3] Изменение названия и описания задачи...")
    
    session = Session()
    response = await chat_with_ai(
        'Измени задачу про звонок клиенту: назови её "Важный звонок Иванову" и добавь описание "Обсудить контракт на 2026 год"',
        user_id=user_id,
        db_session=session
    )
    session.close()
    
    print(f"Ответ AI: {response.get('response', '')[:200]}")
    
    # Проверяем изменения
    session = Session()
    edited_task = session.query(Task).filter_by(id=task_id).first()
    session.close()
    
    if edited_task:
        print_info(f"  - Новое название: {edited_task.title}")
        print_info(f"  - Описание: {edited_task.description or 'Нет'}")
        
        if edited_task.title != task.title:
            print_success("Название изменено")
        else:
            print_error("Название НЕ изменено!")
            
        if edited_task.description:
            print_success("Описание добавлено")
        else:
            print_error("Описание НЕ добавлено!")
    
    # ===== ТЕСТ 4: Список задач =====
    print_info("\n[ТЕСТ 4] Получение списка задач...")
    
    session = Session()
    response = await chat_with_ai(
        'Покажи мои задачи',
        user_id=user_id,
        db_session=session
    )
    session.close()
    
    print(f"Ответ AI: {response.get('response', '')[:400]}")
    
    if 'Иванов' in response.get('response', '') or 'звонок' in response.get('response', '').lower():
        print_success("Задача отображается в списке")
    else:
        print_error("Задача НЕ найдена в списке!")
    
    # ===== ТЕСТ 5: Создание второй задачи =====
    print_info("\n[ТЕСТ 5] Создание второй задачи...")
    
    session = Session()
    response = await chat_with_ai(
        'Добавь задачу "Подготовить отчет" на послезавтра в 10:00',
        user_id=user_id,
        db_session=session
    )
    session.close()
    
    print(f"Ответ AI: {response.get('response', '')[:200]}")
    
    session = Session()
    all_tasks = session.query(Task).filter_by(user_id=user.id).all()
    session.close()
    
    if len(all_tasks) >= 2:
        print_success(f"Создано {len(all_tasks)} задач")
    else:
        print_error(f"Всего только {len(all_tasks)} задача!")
    
    # ===== ТЕСТ 6: Завершение задачи =====
    print_info("\n[ТЕСТ 6] Завершение задачи...")
    
    session = Session()
    response = await chat_with_ai(
        'Отметь задачу "Подготовить отчет" как выполненную',
        user_id=user_id,
        db_session=session
    )
    session.close()
    
    print(f"Ответ AI: {response.get('response', '')[:200]}")
    
    session = Session()
    completed_task = session.query(Task).filter(
        Task.user_id == user.id,
        Task.title.contains('отчет')
    ).first()
    session.close()
    
    if completed_task and completed_task.status == 'completed':
        print_success("Задача помечена выполненной")
    else:
        print_error("Задача НЕ помечена выполненной!")
    
    # ===== ТЕСТ 7: Удаление задачи =====
    print_info("\n[ТЕСТ 7] Удаление задачи...")
    
    session = Session()
    response = await chat_with_ai(
        'Удали задачу про звонок Иванову',
        user_id=user_id,
        db_session=session
    )
    session.close()
    
    print(f"Ответ AI: {response.get('response', '')[:200]}")
    
    session = Session()
    remaining_tasks = session.query(Task).filter_by(user_id=user.id).all()
    deleted_task = session.query(Task).filter_by(id=task_id).first()
    session.close()
    
    if deleted_task is None or deleted_task.status == 'deleted':
        print_success("Задача удалена")
    else:
        print_error("Задача НЕ удалена!")
    
    print_info(f"  - Осталось задач: {len([t for t in remaining_tasks if t.status != 'deleted'])}")
    
    # ===== ТЕСТ 8: Поиск задач =====
    print_info("\n[ТЕСТ 8] Поиск задач по ключевому слову...")
    
    session = Session()
    response = await chat_with_ai(
        'Найди задачи про отчет',
        user_id=user_id,
        db_session=session
    )
    session.close()
    
    print(f"Ответ AI: {response.get('response', '')[:300]}")
    
    if 'отчет' in response.get('response', '').lower():
        print_success("Поиск работает")
    else:
        print_error("Поиск НЕ работает!")
    
    # ===== ФИНАЛЬНАЯ СТАТИСТИКА =====
    print_info("\n" + "="*50)
    print_info("ФИНАЛЬНАЯ СТАТИСТИКА")
    print_info("="*50)
    
    session = Session()
    final_tasks = session.query(Task).filter_by(user_id=user.id).all()
    session.close()
    
    print_info(f"Всего задач в БД: {len(final_tasks)}")
    for t in final_tasks:
        status_icon = "✓" if t.status == 'completed' else "🗑️" if t.status == 'deleted' else "○"
        print_info(f"  {status_icon} {t.title} ({t.status})")
    
    print_success("\n✓ Все тесты завершены!")

if __name__ == "__main__":
    asyncio.run(test_task_operations())
