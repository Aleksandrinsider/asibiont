"""Тест различных фраз для завершения задачи"""
import asyncio
import os
import logging

os.environ['FREE_ACCESS_MODE'] = '1'
logging.basicConfig(level=logging.WARNING)

from models import init_db, Session, User, Task
from ai_integration.chat import chat_with_ai

async def test_completion_response():
    """Тестирует ответ на вопрос 'Задача выполнена?'"""
    init_db()
    session = Session()
    
    user = session.query(User).filter_by(telegram_id=999999).first()
    if not user:
        user = User(telegram_id=999999, username='test_completion')
        session.add(user)
        session.commit()
    
    # Создаем тестовую задачу
    from datetime import datetime, timedelta
    task = Task(
        user_id=user.id,
        title="Тестовая задача",
        status='active',
        reminder_time=datetime.now() + timedelta(hours=1)
    )
    session.add(task)
    session.commit()
    
    test_phrases = [
        "Да выполнена",
        "выполнена",
        "да",
        "да, выполнена",
        "выполнил",
        "сделано",
        "готово",
    ]
    
    print("🧪 ТЕСТ ОТВЕТОВ НА ВОПРОС О ВЫПОЛНЕНИИ ЗАДАЧИ\n")
    
    for phrase in test_phrases:
        print(f"📝 Фраза: '{phrase}'")
        
        response = await chat_with_ai(
            message=phrase,
            user_id=user.telegram_id,
            db_session=session
        )
        
        tool_calls = response.get('tool_calls', [])
        ai_response = response.get('response', '')
        
        if tool_calls:
            func = tool_calls[0].get('function')
            print(f"   ✅ Функция вызвана: {func}")
        else:
            print(f"   ❌ Функция НЕ вызвана")
            
        print(f"   💬 Ответ AI: {ai_response[:100]}...\n")
    
    session.close()

if __name__ == '__main__':
    asyncio.run(test_completion_response())
