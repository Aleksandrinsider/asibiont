#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Полный тест всех команд агента"""

import asyncio
from datetime import datetime
import pytz
from models import Session, Task, User
from ai_integration.chat import chat_with_ai

async def test_all_commands():
    telegram_id = 777888
    session = Session()
    
    # Очистка
    user = session.query(User).filter_by(telegram_id=telegram_id).first()
    if user:
        session.query(Task).filter_by(user_id=user.id).delete()
        session.commit()
    
    print("=" * 80)
    print("ПОЛНЫЙ ТЕСТ ВСЕХ КОМАНД АГЕНТА")
    print("=" * 80)
    
    results = {"✅": 0, "❌": 0, "❓": 0}
    
    # 1. ADD_TASK
    print("\n1️⃣  ADD_TASK - создание задач")
    print("-" * 80)
    
    tests = [
        "Напомни купить хлеб завтра в 9:00",
        "Встреча с клиентом послезавтра в 14:30",
        "Позвонить маме через 2 часа",
    ]
    
    for test in tests:
        response = await chat_with_ai(test, user_id=telegram_id)
        success = "добавлен" in response.lower() or "напоминание" in response.lower() or "создан" in response.lower()
        mark = "✅" if success else "❌"
        results[mark] += 1
        print(f"{mark} {test[:50]}")
    
    session.expire_all()
    user = session.query(User).filter_by(telegram_id=telegram_id).first()
    tasks = session.query(Task).filter_by(user_id=user.id).all()
    print(f"   Создано задач: {len(tasks)}")
    
    # 2. LIST_TASKS
    print("\n2️⃣  LIST_TASKS - показать задачи")
    print("-" * 80)
    
    response = await chat_with_ai("Покажи мои задачи", user_id=telegram_id)
    success = len(response) > 50 and ("задач" in response.lower() or "напоминание" in response.lower())
    mark = "✅" if success else "❌"
    results[mark] += 1
    print(f"{mark} Список задач получен: {len(response)} символов")
    
    # 3. RESCHEDULE_TASK
    print("\n3️⃣  RESCHEDULE_TASK - перенос времени")
    print("-" * 80)
    
    if len(tasks) > 0:
        task = tasks[0]
        old_time = task.reminder_time
        
        response = await chat_with_ai(f"Перенеси {task.title.split()[0]} на 15:00", user_id=telegram_id)
        
        session.expire_all()
        task_updated = session.query(Task).filter_by(id=task.id).first()
        success = task_updated and task_updated.reminder_time != old_time
        mark = "✅" if success else "❌"
        results[mark] += 1
        print(f"{mark} Время изменено: {old_time} → {task_updated.reminder_time if task_updated else 'N/A'}")
    else:
        results["❓"] += 1
        print("❓ Нет задач для переноса")
    
    # 4. EDIT_TASK
    print("\n4️⃣  EDIT_TASK - редактирование")
    print("-" * 80)
    
    if len(tasks) > 1:
        task = tasks[1]
        old_time = task.reminder_time
        
        response = await chat_with_ai(f"Измени время {task.title.split()[0]} на через 1 час", user_id=telegram_id)
        
        session.expire_all()
        task_updated = session.query(Task).filter_by(id=task.id).first()
        success = task_updated and task_updated.reminder_time != old_time
        mark = "✅" if success else "❌"
        results[mark] += 1
        print(f"{mark} Задача отредактирована")
    else:
        results["❓"] += 1
        print("❓ Недостаточно задач для редактирования")
    
    # 5. COMPLETE_TASK
    print("\n5️⃣  COMPLETE_TASK - завершение")
    print("-" * 80)
    
    if len(tasks) > 0:
        task = tasks[0]
        
        response = await chat_with_ai(f"Завершил {task.title.split()[0]}", user_id=telegram_id)
        
        session.expire_all()
        task_completed = session.query(Task).filter_by(id=task.id).first()
        success = task_completed and task_completed.status == "completed"
        mark = "✅" if success else "❌"
        results[mark] += 1
        print(f"{mark} Задача завершена: {task_completed.status if task_completed else 'N/A'}")
    else:
        results["❓"] += 1
        print("❓ Нет задач для завершения")
    
    # 6. GET_TASK_DETAILS
    print("\n6️⃣  GET_TASK_DETAILS - детали задачи")
    print("-" * 80)
    
    session.expire_all()
    tasks = session.query(Task).filter_by(user_id=user.id, status="pending").all()
    
    if len(tasks) > 0:
        task = tasks[0]
        response = await chat_with_ai(f"Расскажи подробнее про {task.title.split()[0]}", user_id=telegram_id)
        success = len(response) > 50
        mark = "✅" if success else "❌"
        results[mark] += 1
        print(f"{mark} Детали получены")
    else:
        results["❓"] += 1
        print("❓ Нет активных задач")
    
    # 7. DELETE_TASK
    print("\n7️⃣  DELETE_TASK - удаление")
    print("-" * 80)
    
    session.expire_all()
    tasks_before = session.query(Task).filter_by(user_id=user.id).count()
    
    if tasks_before > 0:
        tasks = session.query(Task).filter_by(user_id=user.id).all()
        task = tasks[-1]
        
        response = await chat_with_ai(f"Удали {task.title.split()[0]}", user_id=telegram_id)
        
        session.expire_all()
        tasks_after = session.query(Task).filter_by(user_id=user.id).count()
        success = tasks_after < tasks_before or "удален" in response.lower()
        mark = "✅" if success else "❌"
        results[mark] += 1
        print(f"{mark} Задач до/после: {tasks_before}/{tasks_after}")
    else:
        results["❓"] += 1
        print("❓ Нет задач для удаления")
    
    # 8. UPDATE_PROFILE
    print("\n8️⃣  UPDATE_PROFILE - профиль")
    print("-" * 80)
    
    response = await chat_with_ai("Запомни: я работаю программистом в IT компании", user_id=telegram_id)
    success = "профиль" in response.lower() or "запомнил" in response.lower() or "сохранил" in response.lower()
    mark = "✅" if success else "❌"
    results[mark] += 1
    print(f"{mark} Профиль обновлен")
    
    # 9. FIND_PARTNERS
    print("\n9️⃣  FIND_PARTNERS - поиск контактов")
    print("-" * 80)
    
    response = await chat_with_ai("Найди контакты по моим интересам", user_id=telegram_id)
    success = len(response) > 50
    mark = "✅" if success else "❌"
    results[mark] += 1
    print(f"{mark} Поиск выполнен")
    
    # 10. SUGGEST_ALTERNATIVES
    print("\n🔟 SUGGEST_ALTERNATIVES - альтернативы")
    print("-" * 80)
    
    response = await chat_with_ai("Не могу выполнить задачу по работе, посоветуй что делать", user_id=telegram_id)
    success = len(response) > 100
    mark = "✅" if success else "❌"
    results[mark] += 1
    print(f"{mark} Альтернативы предложены")
    
    # 11. BRAINSTORM_IDEAS
    print("\n1️⃣1️⃣ BRAINSTORM_IDEAS - мозговой штурм")
    print("-" * 80)
    
    response = await chat_with_ai("Помоги придумать идеи для стартапа в сфере AI", user_id=telegram_id)
    success = len(response) > 100 and ("идея" in response.lower() or "можно" in response.lower())
    mark = "✅" if success else "❌"
    results[mark] += 1
    print(f"{mark} Идеи сгенерированы")
    
    # 12. DELEGATION - создаём второго пользователя и тестируем
    print("\n1️⃣2️⃣ DELEGATE_TASK - делегирование")
    print("-" * 80)
    
    # Создаём второго пользователя для делегирования
    second_user = session.query(User).filter_by(telegram_id=777889).first()
    if not second_user:
        from models import UserProfile, SubscriptionTier
        second_user = User(telegram_id=777889, username="test_delegate_user")
        session.add(second_user)
        session.commit()
        
        profile = UserProfile(user_id=second_user.id)
        profile.skills = "Python, AI"
        profile.interests = "Программирование, тестирование"
        profile.subscription_tier = SubscriptionTier.STANDARD
        session.add(profile)
        session.commit()
        print("   Создан второй пользователь: @test_delegate_user (ID: 777889)")
    
    # Делегируем задачу
    response = await chat_with_ai("Делегируй задачу 'Код-ревью' пользователю @test_delegate_user на завтра в 10:00", user_id=telegram_id)
    
    # Проверяем делегированную задачу
    session.expire_all()
    delegated_tasks = session.query(Task).filter_by(
        delegated_by=user.id
    ).filter(Task.delegated_to_username == "test_delegate_user").all()
    
    success = len(delegated_tasks) > 0 or "делегирован" in response.lower() or "поручена" in response.lower()
    mark = "✅" if success else "❌"
    results[mark] += 1
    print(f"{mark} Делегирование: найдено {len(delegated_tasks)} делегированных задач")
    
    # ИТОГИ
    print("\n" + "=" * 80)
    print("📊 ИТОГОВАЯ СТАТИСТИКА")
    print("=" * 80)
    print(f"✅ Успешно: {results['✅']}")
    print(f"❌ Провалено: {results['❌']}")
    print(f"❓ Пропущено: {results['❓']}")
    
    total_tested = results['✅'] + results['❌']
    if total_tested > 0:
        success_rate = (results['✅'] / total_tested) * 100
        print(f"\n🎯 Процент успеха: {success_rate:.1f}%")
    
    print("\n" + "=" * 80)
    
    session.close()

if __name__ == "__main__":
    asyncio.run(test_all_commands())
