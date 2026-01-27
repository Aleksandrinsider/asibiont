"""
Быстрый тест команды "Закрой все мои задачи"
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from models import Session, User, Task, UserProfile
from ai_integration.chat import chat_with_ai


async def test_single_command():
    commands = [
        'Закрой все мои задачи',
        'Удали все задачи',
        'Очисти все задачи',
        'Убери все задачи',
        'Удали все мои задачи',
    ]
    
    for i, command in enumerate(commands, 1):
        user_id = 999777 + i
        session = Session()
        
        # Создаем пользователя
        test_user = session.query(User).filter_by(telegram_id=user_id).first()
        if not test_user:
            test_user = User(telegram_id=user_id, username=f'test{i}', first_name='Test', timezone='Europe/Moscow')
            session.add(test_user)
            session.commit()
        
        # Профиль
        if not session.query(UserProfile).filter_by(user_id=test_user.id).first():
            session.add(UserProfile(user_id=test_user.id))
            session.commit()
        
        # Удаляем старые
        session.query(Task).filter_by(user_id=test_user.id).delete()
        session.commit()
        
        # Создаем 3 задачи
        await chat_with_ai('Напомни проверить почту через 10 минут', user_id=user_id, db_session=session)
        await chat_with_ai('Напомни позвонить через 1 час', user_id=user_id, db_session=session)
        await chat_with_ai('Напомни встреча завтра в 10:00', user_id=user_id, db_session=session)
        
        session.expire_all()
        before = session.query(Task).filter_by(user_id=test_user.id, status='pending').count()
        
        # Тест команды
        print(f'\n{i}. "{command}"')
        print(f'   До: {before} задач')
        response = await chat_with_ai(command, user_id=user_id, db_session=session)
        
        # Проверка
        session.expire_all()
        after = session.query(Task).filter_by(user_id=test_user.id, status='pending').count()
        total = session.query(Task).filter_by(user_id=test_user.id).count()
        
        print(f'   После: {after} активных (всего {total})')
        
        if after == 0:
            print(f'   ✅ УСПЕХ')
        else:
            print(f'   ❌ ОШИБКА: осталось {after} задач')
            print(f'   Ответ: {response[:150]}')
        
        session.close()


if __name__ == '__main__':
    asyncio.run(test_single_command())
