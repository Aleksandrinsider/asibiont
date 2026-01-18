import os
from dotenv import load_dotenv
load_dotenv()

from config import DATABASE_URL, LOCAL
print('LOCAL:', LOCAL)
print('DATABASE_URL configured:', bool(DATABASE_URL))

from models import engine
try:
    connection = engine.connect()
    print('✅ Production database connection successful')

    # Проверим количество пользователей
    from sqlalchemy import text
    result = connection.execute(text('SELECT COUNT(*) FROM users'))
    user_count = result.fetchone()[0]
    print(f'Users in production DB: {user_count}')

    # Проверим количество задач
    result = connection.execute(text('SELECT COUNT(*) FROM tasks'))
    task_count = result.fetchone()[0]
    print(f'Tasks in production DB: {task_count}')

    # Проверим задачи с делегированием
    result = connection.execute(text('SELECT COUNT(*) FROM tasks WHERE delegated_to_username IS NOT NULL'))
    delegated_count = result.fetchone()[0]
    print(f'Delegated tasks in production DB: {delegated_count}')

    # Проверим конкретного пользователя
    result = connection.execute(text("SELECT username, telegram_id FROM users WHERE username = 'aleksandrinsider'"))
    user_row = result.fetchone()
    if user_row:
        print(f'User aleksandrinsider found: telegram_id={user_row[1]}')

        # Проверим его задачи
        user_id = connection.execute(text("SELECT id FROM users WHERE username = 'aleksandrinsider'")).fetchone()[0]
        result = connection.execute(text(f'SELECT COUNT(*) FROM tasks WHERE user_id = {user_id}'))
        user_tasks = result.fetchone()[0]
        print(f'Tasks for aleksandrinsider: {user_tasks}')

        # Проверим делегированные ему задачи
        result = connection.execute(text("SELECT COUNT(*) FROM tasks WHERE delegated_to_username = 'aleksandrinsider'"))
        delegated_to_user = result.fetchone()[0]
        print(f'Tasks delegated to aleksandrinsider: {delegated_to_user}')

        # Покажем несколько задач пользователя
        result = connection.execute(text(f'SELECT id, title, status, delegation_status FROM tasks WHERE user_id = {user_id} LIMIT 5'))
        tasks = result.fetchall()
        print(f'Sample tasks for aleksandrinsider:')
        for task in tasks:
            delegation_info = f' (delegation: {task[3]})' if task[3] else ''
            print(f'  - ID {task[0]}: {task[1]} [{task[2]}]{delegation_info}')

    connection.close()
except Exception as e:
    print(f'❌ Production database connection failed: {e}')
    import traceback
    traceback.print_exc()