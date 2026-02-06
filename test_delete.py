"""Финальный тест удаления"""
import asyncio
import os
os.environ['LOCAL'] = '1'

from ai_integration.chat import chat_with_ai
from models import Session, User, Task

async def test_delete():
    user_id = 777888999
    
    # Тест удаления с точным названием
    print('[DELETE TEST] Удаление с точным названием...')
    session = Session()
    r = await chat_with_ai('Удали задачу "Важная почта от клиента"', user_id=user_id, db_session=session)
    session.close()
    print(f'AI Response: {r.get("response", "")[:150]}')
    
    # Проверка
    session = Session()
    user_obj = session.query(User).filter_by(telegram_id=user_id).first()
    if user_obj:
        tasks = session.query(Task).filter_by(user_id=user_obj.id, status='pending').all()
        print(f'\n[RESULT] Активных задач в БД: {len(tasks)}')
        for t in tasks:
            print(f'  - {t.title} ({t.status})')
        
        if len(tasks) == 0:
            print('\n✅ УСПЕШНО: Задача удалена!')
        else:
            print('\n❌ ОШИБКА: Задача НЕ удалена!')
    session.close()

if __name__ == '__main__':
    asyncio.run(test_delete())
