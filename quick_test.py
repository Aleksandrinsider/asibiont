import asyncio
import sys
sys.path.append('.')

from ai_integration.autonomous_agent import chat_with_ai
from models import User, SessionLocal

async def test():
    user = User(id=1, telegram_id=123456789, username='test_user', subscription_tier='STANDARD', created_at='2024-01-01')
    session = SessionLocal()
    result = await chat_with_ai(message='Как приготовить пасту карбонара?', user_id=user.id, db_session=session)
    print('ОТВЕТ АГЕНТА:')
    print(result['response'][:500] + '...')
    print(f'ИНСТРУМЕНТЫ: {len(result.get("tool_calls", []))}')
    session.close()

if __name__ == "__main__":
    asyncio.run(test())