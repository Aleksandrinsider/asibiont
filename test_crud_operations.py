import asyncio
from ai_integration import add_task, edit_task, complete_task, delete_task, update_profile, find_partners, delete_all_tasks
from models import Session, Task, UserProfile, User
import json

# Создаем тестового пользователя
session = Session()
test_user = session.query(User).filter_by(telegram_id=123456789).first()
if not test_user:
    test_user = User(telegram_id=123456789, username='testuser')
    session.add(test_user)
    session.commit()

user_id = test_user.telegram_id
print(f'Тестовый пользователь: {user_id}')

# Тест 1: add_task
print('\n=== ТЕСТ 1: add_task ===')
result = add_task(title='Тестовая задача', reminder_time='через 10 минут', user_id=user_id, session=session)
print(f'add_task результат: {result}')

# Проверить в БД
task = session.query(Task).filter_by(user_id=test_user.id, title='Тестовая задача').first()
if task:
    print(f'✅ Задача в БД: ID={task.id}, title="{task.title}", reminder_time={task.reminder_time}')
else:
    print('❌ Задача НЕ найдена в БД!')

# Тест 2: edit_task
print('\n=== ТЕСТ 2: edit_task ===')
if task:
    original_reminder = task.reminder_time
    result = edit_task(task_title='Тестовая задача', reminder_time='через 20 минут', user_id=user_id, session=session)
    print(f'edit_task результат: {result}')

    # Проверить обновление
    updated_task = session.query(Task).filter_by(id=task.id).first()
    if updated_task and updated_task.reminder_time != original_reminder:
        print(f'✅ Обновленная задача: reminder_time={updated_task.reminder_time} (изменено)')
    else:
        print('❌ reminder_time не обновился!')
else:
    print('❌ Задача не найдена для редактирования!')

# Тест 3: complete_task
print('\n=== ТЕСТ 3: complete_task ===')
if task:
    result = complete_task(task_title='Тестовая задача', user_id=user_id, session=session)
    print(f'complete_task результат: {result}')

    # Проверить статус
    completed_task = session.query(Task).filter_by(id=task.id).first()
    if completed_task and completed_task.status == 'completed' and completed_task.actual_completion_time:
        print(f'✅ Завершенная задача: status={completed_task.status}, completion_time={completed_task.actual_completion_time}')
    else:
        print('❌ Статус задачи не изменился на completed!')
else:
    print('❌ Задача не найдена для завершения!')

# Тест 4: update_profile
print('\n=== ТЕСТ 4: update_profile ===')
result = update_profile(city='Москва', interests='программирование, AI', user_id=user_id)
print(f'update_profile результат: {result}')

# Проверить профиль
profile = session.query(UserProfile).filter_by(user_id=test_user.id).first()
if profile and profile.city == 'Москва' and 'программирование' in profile.interests:
    print(f'✅ Профиль: city={profile.city}, interests={profile.interests}')
else:
    print('❌ Профиль не обновился корректно!')

# Тест 5: find_partners
print('\n=== ТЕСТ 5: find_partners ===')
result = find_partners(user_id=user_id)
if result and len(result) > 10:  # Проверка, что есть содержимое
    print(f'✅ find_partners вернул результаты (длина: {len(result)} символов)')
else:
    print('❌ find_partners не вернул результаты!')

# Тест 6: delete_task (если задача не была удалена)
print('\n=== ТЕСТ 6: delete_task ===')
if task:
    result = delete_task(task_title='Тестовая задача', user_id=user_id, session=session)
    print(f'delete_task результат: {result}')

    # Проверить удаление
    deleted_task = session.query(Task).filter_by(id=task.id).first()
    if not deleted_task:
        print('✅ Задача успешно удалена из БД')
    else:
        print('❌ Задача все еще в БД после удаления!')
else:
    print('❌ Задача не найдена для удаления!')

# Тест 7: delete_all_tasks
print('\n=== ТЕСТ 7: delete_all_tasks ===')
# Сначала создадим несколько задач для теста
print('Создаем тестовые задачи...')
add_task(title='Задача 1', reminder_time='через 1 час', user_id=user_id, session=session)
add_task(title='Задача 2', reminder_time='завтра в 10:00', user_id=user_id, session=session)
add_task(title='Задача 3', reminder_time='через 30 минут', user_id=user_id, session=session)

# Проверить, что задачи созданы
task_count_before = session.query(Task).filter_by(user_id=test_user.id).count()
print(f'Задач до удаления: {task_count_before}')

# Выполнить delete_all_tasks
result = delete_all_tasks(user_id=user_id)
print(f'delete_all_tasks результат: {result}')

# Проверить удаление всех задач
task_count_after = session.query(Task).filter_by(user_id=test_user.id).count()
print(f'Задач после удаления: {task_count_after}')

if task_count_after == 0:
    print('✅ Все задачи успешно удалены из БД')
else:
    print(f'❌ Осталось {task_count_after} задач в БД после delete_all_tasks!')

session.close()
print('\n=== ТЕСТИРОВАНИЕ ЗАВЕРШЕНО ===')