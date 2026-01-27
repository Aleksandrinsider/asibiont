import asyncio
from ai_integration.chat import chat_with_ai
from models import Session, Task, User

async def test():
    session = Session()
    user = session.query(User).first()
    
    if user:
        # Очистка
        old = session.query(Task).filter(Task.user_id == user.id).all()
        for t in old:
            session.delete(t)
        session.commit()
        
        print('Тест 1: напомни через 5 минут проверить почту')
        result = await chat_with_ai(
            message='напомни через 5 минут проверить почту',
            user_id=user.telegram_id,
            db_session=session
        )
        print(f'Ответ: {result[:100]}...')
        
        tasks = session.query(Task).filter(Task.user_id == user.id).all()
        print(f'Создано задач: {len(tasks)}')
        if tasks:
            print(f'✅ Задача создана: {tasks[0].title}')
        else:
            print('❌ Задача НЕ создана!')
        
        print('\nТест 2: через 15 минут нужно заказать продукты')
        result = await chat_with_ai(
            message='через 15 минут нужно заказать продукты',
            user_id=user.telegram_id,
            db_session=session
        )
        print(f'Ответ: {result[:100]}...')
        
        tasks = session.query(Task).filter(Task.user_id == user.id).all()
        print(f'Всего задач: {len(tasks)}')
        for t in tasks:
            print(f'  - {t.title}')
        
        if len(tasks) == 2:
            print('\n✅ ОБЕ ЗАДАЧИ СОЗДАНЫ!')
        else:
            print(f'\n❌ Ожидалось 2 задачи, создано {len(tasks)}')
    
    session.close()

asyncio.run(test())