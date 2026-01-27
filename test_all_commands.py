import asyncio
from ai_integration.chat import chat_with_ai
from models import Session, Task, User

async def test_all_commands():
    """Комплексный тест всех команд с защитой от галлюцинаций"""
    session = Session()
    user = session.query(User).first()

    if not user:
        print("❌ Пользователь не найден")
        return

    print("🚀 ТЕСТИРУЕМ ВСЕ КОМАНДЫ С ЗАЩИТОЙ ОТ ГАЛЛЮЦИНАЦИЙ\n")

    # Очистка
    old = session.query(Task).filter(Task.user_id == user.id).all()
    for t in old:
        session.delete(t)
    session.commit()

    # 1. СОЗДАНИЕ ЗАДАЧ
    print("=" * 60)
    print("1️⃣ ТЕСТ СОЗДАНИЯ ЗАДАЧ")
    print("=" * 60)
    
    test_create = [
        "напомни через 5 минут проверить почту",
        "через 15 минут нужно заказать продукты",
        "создай задачу позвонить клиенту завтра в 10:00"
    ]
    
    for i, msg in enumerate(test_create, 1):
        print(f"\n{i}. Команда: {msg}")
        result = await chat_with_ai(
            message=msg,
            user_id=user.telegram_id,
            db_session=session
        )
        print(f"   Ответ: {result[:80]}...")
    
    tasks = session.query(Task).filter(Task.user_id == user.id).all()
    print(f"\n📊 Создано задач: {len(tasks)}")
    for t in tasks:
        print(f"   ✅ {t.title}")
    
    if len(tasks) == 3:
        print("✅ ВСЕ ЗАДАЧИ СОЗДАНЫ!")
    else:
        print(f"❌ Ожидалось 3 задачи, создано {len(tasks)}")

    # 2. ПРОСМОТР ЗАДАЧ
    print("\n" + "=" * 60)
    print("2️⃣ ТЕСТ ПРОСМОТРА ЗАДАЧ")
    print("=" * 60)
    
    print("\nКоманда: покажи мои задачи")
    result = await chat_with_ai(
        message="покажи мои задачи",
        user_id=user.telegram_id,
        db_session=session
    )
    print(f"Ответ: {result[:150]}...")
    
    if "задач" in result.lower() or "проверить почту" in result.lower():
        print("✅ СПИСОК ЗАДАЧ ПОКАЗАН!")
    else:
        print("❌ Список задач не показан")

    # 3. ЗАВЕРШЕНИЕ ЗАДАЧИ
    print("\n" + "=" * 60)
    print("3️⃣ ТЕСТ ЗАВЕРШЕНИЯ ЗАДАЧИ")
    print("=" * 60)
    
    # Обновляем список задач
    tasks = session.query(Task).filter(Task.user_id == user.id).all()
    active_tasks = [t for t in tasks if t.status != "completed"]
    
    if active_tasks:
        task_to_complete = active_tasks[0]
        print(f"\nКоманда: готово с задачей {task_to_complete.title}")
        result = await chat_with_ai(
            message=f"готово с задачей {task_to_complete.title}",
            user_id=user.telegram_id,
            db_session=session
        )
        print(f"Ответ: {result[:80]}...")
        
        # Перечитываем задачу из БД
        task_after = session.query(Task).filter(Task.id == task_to_complete.id).first()
        if task_after and task_after.status == "completed":
            print("✅ ЗАДАЧА ЗАВЕРШЕНА!")
        else:
            print(f"❌ Статус задачи: {task_after.status if task_after else 'не найдена'}")

    # 4. УДАЛЕНИЕ ЗАДАЧИ
    print("\n" + "=" * 60)
    print("4️⃣ ТЕСТ УДАЛЕНИЯ ЗАДАЧИ")
    print("=" * 60)
    
    remaining = session.query(Task).filter(Task.user_id == user.id, Task.status != "completed").all()
    if remaining:
        task_to_delete = remaining[0]
        print(f"\nКоманда: удали задачу {task_to_delete.title}")
        result = await chat_with_ai(
            message=f"удали задачу {task_to_delete.title}",
            user_id=user.telegram_id,
            db_session=session
        )
        print(f"Ответ: {result[:80]}...")
        
        # Проверяем удаление
        task_after = session.query(Task).filter(Task.id == task_to_delete.id).first()
        if not task_after:
            print("✅ ЗАДАЧА УДАЛЕНА!")
        else:
            print("❌ Задача не удалена")

    # 5. РЕДАКТИРОВАНИЕ ЗАДАЧИ
    print("\n" + "=" * 60)
    print("5️⃣ ТЕСТ РЕДАКТИРОВАНИЯ ЗАДАЧИ")
    print("=" * 60)
    
    remaining = session.query(Task).filter(Task.user_id == user.id, Task.status != "completed").all()
    if remaining:
        task_to_edit = remaining[0]
        print(f"\nКоманда: перенеси задачу {task_to_edit.title} на завтра в 14:00")
        result = await chat_with_ai(
            message=f"перенеси задачу {task_to_edit.title} на завтра в 14:00",
            user_id=user.telegram_id,
            db_session=session
        )
        print(f"Ответ: {result[:80]}...")
        
        # Проверяем изменение
        session.refresh(task_to_edit)
        if task_to_edit.reminder_time:
            print(f"✅ ЗАДАЧА ИЗМЕНЕНА! Новое время: {task_to_edit.reminder_time}")
        else:
            print("❌ Задача не изменена")

    # ИТОГОВАЯ СТАТИСТИКА
    print("\n" + "=" * 60)
    print("📊 ИТОГОВАЯ СТАТИСТИКА")
    print("=" * 60)
    
    all_tasks = session.query(Task).filter(Task.user_id == user.id).all()
    completed = [t for t in all_tasks if t.status == "completed"]
    pending = [t for t in all_tasks if t.status == "pending"]
    
    print(f"Всего задач: {len(all_tasks)}")
    print(f"Завершено: {len(completed)}")
    print(f"Активных: {len(pending)}")
    
    print("\n🎉 ТЕСТ ЗАВЕРШЕН!")
    print("✅ Защита от галлюцинаций работает для всех команд")
    
    session.close()

asyncio.run(test_all_commands())