from models import Session, Task, User, UserProfile
import logging
logging.basicConfig(level=logging.INFO)

print('🔍 АНАЛИЗ ПРОБЛЕМЫ С ДЕЛЕГИРОВАНИЕМ')
print('=' * 50)

session = Session()

try:
    # Проверим все задачи
    all_tasks = session.query(Task).all()
    print(f'Всего задач в БД: {len(all_tasks)}')

    # Задачи без user_id
    tasks_no_user = session.query(Task).filter(Task.user_id.is_(None)).all()
    print(f'Задач без user_id: {len(tasks_no_user)}')

    # Задачи с делегированием
    delegated_tasks = session.query(Task).filter(Task.delegated_by.isnot(None)).all()
    print(f'Делегированных задач: {len(delegated_tasks)}')

    # Задачи, которые делегированы кому-то
    tasks_to_delegate = session.query(Task).filter(Task.delegated_to_username.isnot(None)).all()
    print(f'Задач, делегированных кому-то: {len(tasks_to_delegate)}')

    # Проверим пользователей
    users = session.query(User).all()
    print(f'Всего пользователей: {len(users)}')

    # Проверим статусы делегирования
    statuses = {}
    for task in all_tasks:
        status = task.delegation_status or 'none'
        statuses[status] = statuses.get(status, 0) + 1

    print(f'\n📊 СТАТУСЫ ДЕЛЕГИРОВАНИЯ:')
    for status, count in statuses.items():
        print(f'  {status}: {count} задач')

    print(f'\n🔗 СВЯЗЬ С УДАЛЕНИЕМ ДАННЫХ:')
    if len(tasks_no_user) == 0:
        print('✅ Все задачи имеют user_id - удаление не затронуло делегирование')
    else:
        print('⚠️ Есть задачи без user_id - возможно, делегирование пострадало')

    if len(delegated_tasks) > 0:
        print(f'✅ Есть {len(delegated_tasks)} делегированных задач - функция работает')
    else:
        print('❌ Нет делегированных задач - функция не используется или сломана')

except Exception as e:
    print(f'❌ Ошибка: {e}')
    import traceback
    traceback.print_exc()
finally:
    session.close()