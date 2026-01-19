from models import Session, Task, User
from datetime import datetime, timedelta
import pytz

session = Session()
try:
    # Найдем пользователей
    user1 = session.query(User).filter_by(telegram_id=999999).first()
    user2 = session.query(User).filter_by(telegram_id=1001).first()

    if not user1 or not user2:
        print('Пользователи не найдены')
        exit()

    print(f'Создаем делегированную задачу от {user1.username} к {user2.username}')

    # Создаем делегированную задачу
    from ai_integration.handlers import delegate_task
    result = delegate_task(
        title='Тестовая делегированная задача',
        reminder_time='2026-01-20 15:00',
        delegated_to_username=user2.username,
        user_id=user1.telegram_id,
        description='Тестовое описание',
        delegation_details='Тестовые детали делегирования'
    )
    print(f'Результат делегирования: {result}')

    # Проверим созданную задачу
    delegated_tasks = session.query(Task).filter(
        Task.delegation_status.isnot(None)
    ).all()
    print(f'\nПосле создания найдено делегированных задач: {len(delegated_tasks)}')
    for task in delegated_tasks:
        delegator = session.query(User).filter_by(id=task.user_id).first()
        print(f'  - Задача {task.id}: "{task.title}"')
        print(f'    От: {delegator.username if delegator else "Unknown"}')
        print(f'    Кому: {task.delegated_to_username}')
        print(f'    Статус: {task.delegation_status}')
        print(f'    Дедлайн: {task.reminder_time}')

    # Проверим планировщик
    from main import reminder_service
    if reminder_service and reminder_service.scheduler:
        jobs = reminder_service.scheduler.get_jobs()
        delegation_jobs = [j for j in jobs if 'delegation' in j.id]
        print(f'\nЗапланировано делегационных джобов: {len(delegation_jobs)}')
        for job in delegation_jobs:
            print(f'  - {job.id}: {job.next_run_time}')

finally:
    session.close()