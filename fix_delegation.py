from models import Session, User
import logging
logging.basicConfig(level=logging.INFO)

print('🔧 ИСПРАВЛЕНИЕ ПРОБЛЕМЫ С ПОЛЬЗОВАТЕЛЯМИ')
print('=' * 45)

session = Session()

try:
    users = session.query(User).all()
    print(f'Всего пользователей: {len(users)}')

    # Исправим usernames для первых 5 пользователей
    test_usernames = ['test_user_1', 'test_user_2', 'test_user_3', 'test_user_4', 'test_user_5']

    for i, user in enumerate(users[:5]):
        old_username = user.username
        if not user.username:
            user.username = test_usernames[i]
            print(f'✅ Установлен username "{user.username}" для пользователя {user.telegram_id} (было: {old_username})')
        else:
            print(f'ℹ️ Username уже есть: {user.username} для пользователя {user.telegram_id}')

    session.commit()
    print('✅ Usernames обновлены')

    # Теперь протестируем делегирование
    print('\n🧪 ТЕСТИРУЕМ ДЕЛЕГИРОВАНИЕ...')
    from ai_integration.handlers import delegate_task

    if len(users) >= 2:
        user1 = users[0]
        user2 = users[1]
        print(f'Делегируем от {user1.username} к {user2.username}')

        result = delegate_task(
            title='Тестовая задача после исправления',
            reminder_time='2026-02-15 10:00',
            delegated_to_username=user2.username,
            user_id=user1.telegram_id,
            description='Тест после исправления usernames'
        )

        print(f'Результат: {result}')

        # Проверим результат
        from models import Task
        new_tasks = session.query(Task).filter(Task.delegated_by == user1.id).all()
        print(f'Создано делегированных задач: {len(new_tasks)}')

        if new_tasks:
            for task in new_tasks[-1:]:  # Покажем последнюю
                print(f'✅ Задача создана: "{task.title}" -> @{task.delegated_to_username}')
        else:
            print('❌ Задача не создана')

    session.commit()

except Exception as e:
    print(f'❌ Ошибка: {e}')
    import traceback
    traceback.print_exc()
    session.rollback()
finally:
    session.close()