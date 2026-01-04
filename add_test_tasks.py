"""
Добавляет тестовые задачи для проверки статистики
"""
from models import Session, Task, User, Subscription, UserProfile
from datetime import datetime, timedelta
import pytz

def add_test_tasks():
    session = Session()

    try:
        # Найти тестового пользователя
        user = session.query(User).filter_by(telegram_id=146333757).first()
        if not user:
            print("Пользователь не найден, создаем...")
            user = User(
                telegram_id=146333757,
                username='aleksandrinsider',
                first_name='Aleksandr',
                timezone='Europe/Moscow'
            )
            session.add(user)
            session.flush()

            profile = UserProfile(
                user_id=user.id,
                city='Москва',
                interests='AI, стартапы',
                skills='Python, менеджмент',
                goals='Запустить MVP'
            )
            session.add(profile)

            subscription = Subscription(
                user_id=user.id,
                status='active',
                start_date=datetime.now(pytz.UTC),
                end_date=datetime.now(pytz.UTC) + timedelta(days=30)
            )
            session.add(subscription)
            session.commit()

        print(f"Пользователь найден: {user.id}")

        # Очистить существующие задачи
        session.query(Task).filter_by(user_id=user.id).delete()
        session.commit()

        # Создать тестовые задачи
        now = datetime.now(pytz.UTC)
        moscow_tz = pytz.timezone('Europe/Moscow')

        tasks_data = [
            {
                'title': 'Встреча с командой',
                'status': 'pending',
                'reminder_time': now + timedelta(days=1, hours=2)  # Завтра в 10:00 по Москве
            },
            {
                'title': 'Позвонить клиенту',
                'status': 'pending',
                'reminder_time': now + timedelta(hours=2)  # Через 2 часа
            },
            {
                'title': 'Подготовить презентацию',
                'status': 'completed',
                'reminder_time': now - timedelta(days=1)  # Вчера
            },
            {
                'title': 'Изучить документацию',
                'status': 'pending',
                'reminder_time': now + timedelta(days=2)  # Послезавтра
            },
            {
                'title': 'Отправить отчет',
                'status': 'completed',
                'reminder_time': now - timedelta(hours=5)  # 5 часов назад
            }
        ]

        for task_data in tasks_data:
            task = Task(
                user_id=user.id,
                title=task_data['title'],
                status=task_data['status'],
                reminder_time=task_data['reminder_time']
            )
            session.add(task)

        session.commit()
        print(f"Добавлено {len(tasks_data)} тестовых задач")

        # Проверить
        tasks = session.query(Task).filter_by(user_id=user.id).all()
        print(f"Всего задач в БД: {len(tasks)}")
        for task in tasks:
            print(f"  - {task.title}: {task.status}, reminder: {task.reminder_time}")

    finally:
        session.close()

if __name__ == "__main__":
    add_test_tasks()