"""Финальный комплексный тест всех функций"""
import asyncio
import os
os.environ['LOCAL'] = '1'

from ai_integration.chat import chat_with_ai
from models import Session, User, UserProfile, Task, Base, engine

def test_print(icon, msg):
    print(f"{icon} {msg}")

async def test_all():
    user_id = 888999000
    
    # Инициализация
    Base.metadata.create_all(engine)
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id, username='test_all', first_name='TestAll', timezone='Europe/Moscow')
        session.add(user)
        session.commit()
        profile = UserProfile(user_id=user.id, city='Москва', goals='Полное тестирование')
        session.add(profile)
        session.commit()
    
    # Очистка
    user_db_id = user.id  # Сохраняем ID перед закрытием сессии
    session.query(Task).filter_by(user_id=user_db_id).delete()
    session.commit()
    session.close()
    
    test_print("🔹", "="*60)
    test_print("📋", "КОМПЛЕКСНЫЙ ТЕСТ ВСЕХ ФУНКЦИЙ")
    test_print("🔹", "="*60)
    
    # ТЕСТ 1: Создание задачи
    test_print("\n✅", "[1/10] Создание задачи...")
    session = Session()
    r = await chat_with_ai('Создай задачу "Встреча с партнером" завтра в 15:00', user_id=user_id, db_session=session)
    session.close()
    test_print("📝", f"AI: {r.get('response', '')[:80]}...")
    
    # Проверка
    session = Session()
    user_obj = session.query(User).filter_by(telegram_id=user_id).first()
    tasks = session.query(Task).filter_by(user_id=user_obj.id, status='pending').all() if user_obj else []
    session.close()
    if len(tasks) == 1:
        test_print("✔️", f"Задача создана: {tasks[0].title}")
    else:
        test_print("❌", f"Ошибка создания! Задач: {len(tasks)}")
    
    # ТЕСТ 2: Список задач
    test_print("\n✅", "[2/10] Список задач...")
    session = Session()
    r = await chat_with_ai('Покажи мои задачи', user_id=user_id, db_session=session)
    session.close()
    test_print("📝", f"AI: {r.get('response', '')[:80]}...")
    if 'встреча' in r.get('response', '').lower() or 'партнер' in r.get('response', '').lower():
        test_print("✔️", "Задача найдена в списке")
    else:
        test_print("❌", "Задача НЕ найдена в списке")
    
    # ТЕСТ 3: Редактирование (точное название)
    test_print("\n✅", "[3/10] Редактирование задачи...")
    session = Session()
    r = await chat_with_ai('Измени задачу "Встреча с партнером" - назови её "Важная встреча с инвестором"', user_id=user_id, db_session=session)
    session.close()
    test_print("📝", f"AI: {r.get('response', '')[:80]}...")
    
    user_obj = session.query(User).filter_by(telegram_id=user_id).first()
    task = session.query(Task).filter_by(user_id=user_obj.id, status='pending').first() if user_obj else None
    task = session.query(Task).filter_by(user_id=user.id, status='pending').first()
    session.close()
    if task and 'инвестор' in task.title.lower():
        test_print("✔️", f"Название изменено: {task.title}")
    else:
        test_print("❌", f"Название НЕ изменено: {task.title if task else 'Нет задачи'}")
    
    # ТЕСТ 4: Добавление описания
    test_print("\n✅", "[4/10] Добавление описания...")
    session = Session()
    r = await chat_with_ai('Добавь описание к задаче про встречу: "Обсудить финансирование на 2026 год"', user_id=user_id, db_session=session)
    session.close()
    test_print("📝", f"AI: {r.get('response', '')[:80]}...")
    
    session = Session()
    user_obj = session.query(User).filter_by(telegram_id=user_id).first()
    task = session.query(Task).filter_by(user_id=user_obj.id, status='pending').first() if user_obj else None
    session.close()
    if task and task.description and 'финансиров' in task.description.lower():
        test_print("✔️", f"Описание добавлено: {task.description[:50]}...")
    else:
        test_print("❌", f"Описание НЕ добавлено: {task.description if task and task.description else 'Пусто'}")
    
    # ТЕСТ 5: Перенос времени
    test_print("\n✅", "[5/10] Перенос задачи...")
    session = Session()
    old_reminder = task.reminder_time if task else None
    r = await chat_with_ai('Перенеси встречу на послезавтра в 11:00', user_id=user_id, db_session=session)
    session.close()
    test_print("📝", f"AI: {r.get('response', '')[:80]}...")
    
    session = Session()
    task = session.query(Task).filter_by(user_id=user.id, status='pending').first()
    session.close()
    if task and task.reminder_time != old_reminder:
        test_print("✔️", f"Время изменено: {task.reminder_time}")
    else:
        test_print("❌", "Время НЕ изменено")
    
    # ТЕСТ 6: Создание второй задачи
    test_print("\n✅", "[6/10] Создание второй задачи...")
    session = Session()
    r = await chat_with_ai('Напомни купить продукты сегодня в 18:00', user_id=user_id, db_session=session)
    session.close()
    test_print("📝", f"AI: {r.get('response', '')[:80]}...")
    
    session = Session()
    tasks = session.query(Task).filter_by(user_id=user.id, status='pending').all()
    session.close()
    if len(tasks) == 2:
        test_print("✔️", f"Создано {len(tasks)} задач")
    else:
        test_print("❌", f"Ошибка! Задач: {len(tasks)}")
    
    # ТЕСТ 7: Завершение задачи
    test_print("\n✅", "[7/10] Завершение задачи...")
    session = Session()
    r = await chat_with_ai('Я купил продукты, задача выполнена', user_id=user_id, db_session=session)
    session.close()
    test_print("📝", f"AI: {r.get('response', '')[:80]}...")
    
    session = Session()
    user_obj = session.query(User).filter_by(telegram_id=user_id).first()
    completed = session.query(Task).filter_by(user_id=user_obj.id, status='completed').count() if user_obj else 0
    pending = session.query(Task).filter_by(user_id=user_obj.id, status='pending').count() if user_obj else 0
    session.close()
    if completed == 1 and pending == 1:
        test_print("✔️", f"Завершено: {completed}, Активных: {pending}")
    else:
        test_print("❌", f"Ошибка! Завершено: {completed}, Активных: {pending}")
    
    # ТЕСТ 8: Удаление задачи
    test_print("\n✅", "[8/10] Удаление задачи...")
    session = Session()
    r = await chat_with_ai('Удали задачу про встречу с инвестором', user_id=user_id, db_session=session)
    session.close()
    test_print("📝", f"AI: {r.get('response', '')[:80]}...")
    
    session = Session()
    tasks = session.query(Task).filter_by(user_id=user.id, status='pending').all()
    session.close()
    if len(tasks) == 0:
        test_print("✔️", "Задача удалена")
    else:
        test_print("❌", f"Задача НЕ удалена! Осталось: {len(tasks)}")
    
    # ТЕСТ 9: Общение (без команд)
    test_print("\n✅", "[9/10] Общение с AI...")
    session = Session()
    r = await chat_with_ai('Как погода в Москве?', user_id=user_id, db_session=session)
    session.close()
    test_print("📝", f"AI: {r.get('response', '')[:80]}...")
    if len(r.get('response', '')) > 20:
        test_print("✔️", "AI отвечает корректно")
    else:
        test_print("❌", "Короткий ответ AI")
    
    # ТЕСТ 10: Профиль
    test_print("\n✅", "[10/10] Обновление профиля...")
    session = Session()
    r = await chat_with_ai('Я интересуюсь искусственным интеллектом', user_id=user_id, db_session=session)
    session.close()
    test_print("📝", f"AI: {r.get('response', '')[:80]}...")
    
    session = Session()
    user_obj = session.query(User).filter_by(telegram_id=user_id).first()
    if user_obj:
        profile = session.query(UserProfile).filter_by(user_id=user_obj.id).first()
        if profile and profile.interests and 'интеллект' in profile.interests.lower():
            test_print("✔️", f"Интерес добавлен: {profile.interests}")
        else:
            test_print("⚠️", f"Интерес не добавлен: {profile.interests if profile else 'Нет профиля'}")
    session.close()
    
    # ФИНАЛ
    test_print("\n" + "="*60)
    test_print("🎉", "ТЕСТИРОВАНИЕ ЗАВЕРШЕНО!")
    test_print("="*60, "")
    
    session = Session()
    user_obj = session.query(User).filter_by(telegram_id=user_id).first()
    if user_obj:
        all_tasks = session.query(Task).filter_by(user_id=user_obj.id).all()
        test_print("📊", f"Всего задач в БД: {len(all_tasks)}")
        for t in all_tasks:
            status_icon = "✅" if t.status == 'completed' else "🗑️" if t.status == 'deleted' else "⏳"
            test_print("  ", f"{status_icon} {t.title[:50]} ({t.status})")
    session.close()

if __name__ == '__main__':
    asyncio.run(test_all())
