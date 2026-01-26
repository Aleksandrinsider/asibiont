import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import Session, User, Task
import datetime

async def test_completion():
    session = Session()
    try:
        # Создаем тестового пользователя
        user = session.query(User).filter_by(telegram_id=123456789).first()
        if not user:
            user = User(telegram_id=123456789, username='test_user')
            session.add(user)
            session.commit()

        # Создаем тестовую задачу
        task = Task(
            user_id=user.id,
            title='Забрать сына из школы',
            status='pending',
            reminder_time=datetime.datetime.now() + datetime.timedelta(hours=1)
        )
        session.add(task)
        session.commit()

        print(f'Создана тестовая задача: {task.title} (ID: {task.id})')

        # Тестируем естественное завершение
        test_messages = [
            'я забрал сына из школы',
            'забрал ребенка',
            'сын уже дома',
            'вернулся со школы'
        ]

        for i, msg in enumerate(test_messages):
            print(f'\nТестирую: "{msg}"')
            try:
                # Создаем новую задачу для каждого теста
                test_task = Task(
                    user_id=user.id,
                    title='Забрать сына из школы',
                    status='pending',
                    reminder_time=datetime.datetime.now() + datetime.timedelta(hours=1)
                )
                session.add(test_task)
                session.commit()
                
                print(f'Создана задача ID: {test_task.id}')
                
                response = await chat_with_ai(msg, user_id=user.telegram_id, db_session=session)
                print(f'Ответ AI: {response[:150]}...')
                
                # Проверяем статус задачи
                session.refresh(test_task)
                print(f'Статус задачи после: {test_task.status}')
                
            except Exception as e:
                print(f'Ошибка: {e}')

    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_completion())