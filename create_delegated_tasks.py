import os
os.environ['LOCAL'] = '0'  # Устанавливаем продакшен режим

from models import Session, Task, User
from datetime import datetime
import pytz

session = Session()

# Найдем пользователя aleksandrinsider
user = session.query(User).filter_by(username='aleksandrinsider').first()
if not user:
    print('Пользователь aleksandrinsider не найден')
else:
    print(f'Найден пользователь: {user.username}, ID: {user.id}')

    # Создадим 3 делегированные задачи
    tasks_data = [
        {
            'title': 'Подготовить презентацию по проекту AI-ассистента',
            'description': 'Создать подробную презентацию с демонстрацией всех функций AI-ассистента для команды разработчиков',
            'delegated_to_username': 'aleksandrinsider',
            'delegation_status': 'pending',
            'status': 'pending'
        },
        {
            'title': 'Проанализировать производительность системы',
            'description': 'Провести анализ производительности приложения, выявить узкие места и предложить оптимизации',
            'delegated_to_username': 'aleksandrinsider',
            'delegation_status': 'pending',
            'status': 'pending'
        },
        {
            'title': 'Обновить документацию API',
            'description': 'Привести в порядок и обновить документацию по всем API endpoints с примерами использования',
            'delegated_to_username': 'aleksandrinsider',
            'delegation_status': 'pending',
            'status': 'pending'
        }
    ]

    for task_data in tasks_data:
        task = Task(
            user_id=user.id,  # Используем ID найденного пользователя
            title=task_data['title'],
            description=task_data['description'],
            delegated_to_username=task_data['delegated_to_username'],
            delegation_status=task_data['delegation_status'],
            status=task_data['status'],
            reminder_time=datetime.now(pytz.UTC)
        )
        session.add(task)
        print(f'Создана задача: "{task.title}" (status: {task.delegation_status})')

    session.commit()
    print('\nВсе 3 задачи успешно добавлены в продакшен базу данных!')
    print('Теперь @aleksandrinsider может их принять через бота или интерфейс.')

session.close()