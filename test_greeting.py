import asyncio
from ai_integration.chat import chat_with_ai
from models import User, UserProfile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

async def test_greeting():
    engine = create_engine('sqlite:///tasks.db')
    Session = sessionmaker(bind=engine)
    session = Session()
    
    # Используем существующего тестового пользователя
    user = session.query(User).filter_by(telegram_id=27).first()
    
    print("=" * 80)
    print("ТЕСТ ПРИВЕТСТВИЯ (новый пользователь)")
    print("=" * 80)
    
    response = await chat_with_ai('привет', user_id=27, context=[])
    
    print("\nОТВЕТ АГЕНТА:")
    print("-" * 80)
    print(response)
    print("-" * 80)
    print(f"\nСтатистика:")
    print(f"  Длина: {len(response)} символов")
    print(f"  Предложений: {response.count('.')}")
    print(f"  Вопросов: {response.count('?')}")
    
    session.close()

if __name__ == "__main__":
    asyncio.run(test_greeting())
