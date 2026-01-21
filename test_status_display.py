"""
Тест отображения статусов задач на фронтенде
"""
import os
os.environ['LOCAL'] = '1'

from models import Session, User, Task

session = Session()
user = session.query(User).filter_by(telegram_id=999001).first()

if not user:
    print("❌ Пользователь не найден")
    session.close()
    exit(1)

print("=" * 80)
print("ПРОВЕРКА СТАТУСОВ ЗАДАЧ НА ФРОНТЕНДЕ")
print("=" * 80)

# Получаем все задачи как это делает API
tasks = session.query(Task).filter(
    (Task.user_id == user.id) | (Task.delegated_to_username == user.username)
).all()

print(f"\n📋 Всего задач для отображения: {len(tasks)}")
print("\nПроверка логики отображения статусов:")
print("-" * 80)

for task in tasks:
    # Симулируем логику фронтенда из renderTaskItem
    status_str = 'В работе'
    status_color = '#068487'
    
    # Определяем delegated_by (как в API)
    delegated_by = None
    if task.delegated_to_username and user.username:
        if task.delegated_to_username.lower().strip('@') == user.username.lower():
            creator = session.query(User).filter_by(id=task.user_id).first()
            if creator and creator.username:
                delegated_by = creator.username
    
    delegated_to = task.delegated_to_username
    
    # Логика из template (исправленный порядок - сначала delegated_by!)
    if delegated_by and task.delegation_status == 'pending':
        status_str = 'Ожидает принятия'
        status_color = '#D7AC0C'
    elif delegated_to and task.delegation_status == 'pending':
        status_str = 'Ожидает подтверждения'
        status_color = '#D7AC0C'
    elif task.delegation_status == 'rejected':
        status_str = 'Отклонена'
        status_color = '#d73a49'
    elif task.delegation_status == 'pending':
        status_str = 'Ожидает подтверждения'
        status_color = '#D7AC0C'
    elif task.status == 'completed':
        status_str = 'Завершена'
        status_color = '#238636'
    # overdue проверяется на фронтенде, здесь пропускаем
    
    # Выводим результат
    title = task.title[:50]
    direction = ""
    if delegated_to:
        if delegated_by:
            direction = f" ← от @{delegated_by}"
        else:
            direction = f" → на @{delegated_to}"
    
    db_info = f"status={task.status}"
    if task.delegation_status:
        db_info += f", delegation_status={task.delegation_status}"
    
    print(f"\n{title}{direction}")
    print(f"  БД: {db_info}")
    print(f"  Отображение: {status_str} (цвет: {status_color})")
    
    # Проверяем правильность
    if task.title.startswith('ТЕСТ:'):
        expected = None
        if 'ожидает принятия' in task.title.lower():
            expected = 'Ожидает подтверждения'
        elif 'входящая' in task.title.lower():
            expected = 'Ожидает принятия'
        elif 'отклонена' in task.title.lower():
            expected = 'Отклонена'
        elif 'принята' in task.title.lower():
            expected = 'В работе'
        elif 'завершенная' in task.title.lower():
            expected = 'Завершена'
        elif 'обычная' in task.title.lower():
            expected = 'В работе'
        
        if expected and status_str != expected:
            print(f"  ⚠️  ОШИБКА: Ожидается '{expected}', отображается '{status_str}'")
        elif expected:
            print(f"  ✅ Корректно")

session.close()

print("\n" + "=" * 80)
print("РЕЗЮМЕ")
print("=" * 80)
print("""
Логика отображения статусов (в порядке проверки):

1. delegated_to + delegation_status='pending' → 🟡 Ожидает подтверждения
2. delegated_by + delegation_status='pending' → 🟡 Ожидает принятия  
3. delegation_status='rejected' → 🔴 Отклонена
4. delegation_status='pending' → 🟡 Ожидает подтверждения
5. status='completed' → 🟢 Завершена
6. overdue=true → 🔴 Просрочена
7. По умолчанию → 🔵 В работе
""")
