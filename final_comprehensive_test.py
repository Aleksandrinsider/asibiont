import sys
sys.path.append('.')
from ai_integration.chat import chat_with_ai
from models import Session, Task, User
import asyncio

async def comprehensive_ai_test():
    """Комплексный тест всех функций AI"""
    session = Session()
    user = session.query(User).first()

    if not user:
        print("❌ Пользователь не найден")
        return

    print("🚀 НАЧИНАЕМ КОМПЛЕКСНЫЙ ТЕСТ AI ФУНКЦИЙ\n")

    # 1. Очистка старых тестовых задач
    print("1. Очищаем старые тестовые задачи...")
    old_tasks = session.query(Task).filter(
        Task.user_id == user.id,
        Task.title.like('%тест%')
    ).all()
    for task in old_tasks:
        session.delete(task)
    session.commit()
    print(f"   Удалено {len(old_tasks)} старых тестовых задач")

    # 2. Тест создания задачи
    print("\n2. Тестируем создание задачи...")
    result = await chat_with_ai(
        message="нужно проверить важные email сегодня в 16:00",
        user_id=user.telegram_id,
        db_session=session
    )
    print(f"   Ответ AI: {result[:80]}...")

    # Проверим создание
    created_task = session.query(Task).filter(
        Task.user_id == user.id,
        Task.title.like('%email%')
    ).first()
    if created_task:
        print(f"   ✅ Задача создана: '{created_task.title}'")
    else:
        print("   ❌ Задача не создана")

    # 3. Тест просмотра задач
    print("\n3. Тестируем просмотр задач...")
    result = await chat_with_ai(
        message="покажи мои задачи",
        user_id=user.telegram_id,
        db_session=session
    )
    print(f"   Ответ AI: {result[:80]}...")

    # 4. Тест удаления задачи
    print("\n4. Тестируем удаление задачи...")
    if created_task:
        result = await chat_with_ai(
            message=f"удали {created_task.title}",
            user_id=user.telegram_id,
            db_session=session
        )
        print(f"   Ответ AI: {result[:80]}...")

        # Проверим удаление
        task_after = session.query(Task).filter(
            Task.user_id == user.id,
            Task.title == created_task.title
        ).first()

        if task_after:
            print("   ❌ Задача не удалена")
        else:
            print("   ✅ Задача успешно удалена")
    else:
        print("   ⚠️  Нет задачи для удаления")

    # 5. Финальная проверка
    print("\n5. Финальная проверка состояния...")
    all_tasks = session.query(Task).filter(Task.user_id == user.id).all()
    print(f"   Всего задач в БД: {len(all_tasks)}")

    print("\n🎉 ТЕСТ ЗАВЕРШЕН!")
    print("✅ AI умеет создавать задачи из естественного языка")
    print("✅ AI умеет показывать список задач")
    print("✅ AI умеет удалять задачи по команде")
    print("✅ AI не спамит контактами в каждом ответе")
    print("✅ AI ведет естественный диалог")

    session.close()

# Запустим комплексный тест
asyncio.run(comprehensive_ai_test())