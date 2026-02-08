"""Быстрый тест приветствия"""
import asyncio
import sys
import os
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ai_integration.chat import chat_with_ai
from models import Session, User, UserProfile, Base, engine

async def test_greeting():
    """Тестирует приветствие бота"""
    Base.metadata.create_all(bind=engine)
    
    session = Session()
    try:
        # Создаём тестового пользователя
        user = session.query(User).filter_by(telegram_id=999999).first()
        if not user:
            user = User(telegram_id=999999, username="test_greeting_user")
            session.add(user)
            session.flush()
        
        # Профиль с проектом и задачами
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            profile = UserProfile(
                user_id=user.id,
                company="ASI Biont",
                position="Основатель",
                interests="ИИ, стартапы, бизнес",
                goals="Привлечь 100 пользователей за месяц. Запустить реферальную программу."
            )
            session.add(profile)
        else:
            profile.company = "ASI Biont"
            profile.position = "Основатель"
            profile.interests = "ИИ, стартапы, бизнес"
            profile.goals = "Привлечь 100 пользователей за месяц. Запустить реферальную программу."
        
        session.commit()
        
        # Отправляем приветствие
        print("=" * 80)
        print("[TEST] ПРИВЕТСТВИЕ БОТА")
        print("=" * 80)
        print("\n[USER] Привет\n")
        
        response = await chat_with_ai(
            message="Привет",
            user_id=999999  # telegram_id
        )
        
        print(f"[BOT] {response}\n")
        print("=" * 80)
        
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_greeting())
