import os
from sqlalchemy import create_engine, text

# Используем публичный URL для подключения извне
db_url = os.environ.get('DATABASE_PUBLIC_URL', os.environ.get('DATABASE_URL'))
engine = create_engine(db_url)
conn = engine.connect()

# Проверяем тестовых пользователей - БЕЗ @ в начале!
result = conn.execute(text(
    'SELECT id, username, telegram_id FROM users WHERE username LIKE \'%basketball%\' OR username LIKE \'%marathon%\' OR username LIKE \'%badminton%\' OR username = \'@aleksandrinsider\' LIMIT 10'
))

rows = result.fetchall()
print('User ID | Username | Telegram ID')
print('-' * 60)
for r in rows:
    print(f'{r[0]:7} | {r[1]:30} | {r[2]}')

print('\n\nВСЕ делегированные задачи:')
result3 = conn.execute(text(
    'SELECT t.id, t.title, t.delegated_to_username, t.delegation_status, t.delegated_by, u.username '
    'FROM tasks t '
    'LEFT JOIN users u ON t.delegated_by = u.id '
    'WHERE t.delegation_status = \'pending\' '
    'ORDER BY t.created_at DESC LIMIT 10'
))

rows3 = result3.fetchall()
print('Task ID | Title | To Username | Status | By ID | By Username')
print('-' * 120)
for r in rows3:
    title = r[1][:30] if r[1] else ''
    to_user = r[2] if r[2] else 'None'
    status = r[3] if r[3] else 'None'
    by_id = str(r[4]) if r[4] else 'None'
    by_username = r[5] if r[5] else 'None'
    print(f'{r[0]:7} | {title:30} | {to_user:20} | {status:10} | {by_id:6} | {by_username}')

conn.close()
