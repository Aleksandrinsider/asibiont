import asyncio
import sys
sys.path.append('.')
from ai_integration.chat import chat_with_ai
from models import User, Task, SessionLocal

async def test_task_creation():
    session = SessionLocal()
    user = session.query(User).filter_by(telegram_id=123456789).first()
    if not user:
        user = User(telegram_id=123456789, username='test_user')
        session.add(user)
        session.commit()

    # Очищаем задачи
    session.query(Task).filter_by(user_id=user.id).delete()
    session.commit()

    message = 'надо проверить почту'
    print(f'Тестируем: {message}')

    result = await chat_with_ai(message, user_id=user.telegram_id, db_session=session)
    print(f'Ответ AI: {result[:150]}...')

    tasks = session.query(Task).filter_by(user_id=user.id).filter(Task.status.in_(['active', 'pending'])).all()
    print(f'Создано задач: {len(tasks)}')
    for task in tasks:
        print(f'Задача: "{task.title}"')

    session.close()

if __name__ == "__main__":
    asyncio.run(test_task_creation())