#!/usr/bin/env python3
"""
Full execution test of all commands with real database
"""
import asyncio
import os
os.environ['LOCAL'] = '1'
from ai_integration.router import CommandRouter
from models import Session, Task, User

async def full_execution_test():
    router = CommandRouter()
    session = Session()

    # Create test user
    import random
    user_id = random.randint(100000, 999999)
    test_user = User(telegram_id=user_id, username='test_user')
    session.add(test_user)
    session.commit()

    print(f'🧪 Полный тест выполнения команд для user_id={user_id}')
    print('=' * 60)

    # Test sequence
    test_sequence = [
        ('создай задачу купить молоко через 2 часа', 'CreateTaskCommand'),
        ('покажи мои задачи', 'ListTasksCommand'),
        ('готово купить молоко', 'CompleteTaskCommand'),
        ('покажи мои задачи', 'ListTasksCommand'),
        ('создай задачу позвонить другу завтра в 10:00', 'CreateTaskCommand'),
        ('список дел', 'ListTasksCommand'),
        ('завершил позвонить другу', 'CompleteTaskCommand'),
        ('удали задачу купить молоко', 'DeleteTaskCommand'),
        ('мои задачи', 'ListTasksCommand'),
        ('привет, как дела?', 'ConversationCommand'),
    ]

    for i, (msg, expected_cmd) in enumerate(test_sequence, 1):
        print(f'{i}. "{msg}"')
        print(f'   Ожидается: {expected_cmd}')

        try:
            cmd = await router.route(msg, user_id)
            print(f'   Маршрутизировано: {type(cmd).__name__}')

            result = await cmd.execute(user_id, session)
            print(f'   ✅ Выполнено: {result[:100]}...' if len(str(result)) > 100 else f'   ✅ Выполнено: {result}')

        except Exception as e:
            print(f'   ❌ Ошибка: {e}')

        # Show current DB state
        tasks = session.query(Task).filter_by(user_id=test_user.id).all()
        active = [t for t in tasks if t.status == 'pending']
        completed = [t for t in tasks if t.status == 'completed']
        print(f'   📊 БД: {len(active)} активных, {len(completed)} выполненных')
        print()

    print('🎉 Тест завершен!')
    session.close()

if __name__ == "__main__":
    asyncio.run(full_execution_test())