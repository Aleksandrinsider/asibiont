#!/usr/bin/env python3
"""
Тест доступа к новостям для всех тарифов
"""

import os
import sys
import asyncio
sys.path.append('.')

from ai_integration.handlers import get_news_trends
from models import User, UserProfile, Subscription, SubscriptionTier, init_db
from config import DATABASE_URL
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

async def test_news_access():
    """Тестируем доступ к новостям для разных тарифов"""

    # Инициализируем базу данных
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Найдем пользователей разных тарифов
        users_to_test = []

        # LIGHT пользователь
        light_user = session.query(User).filter_by(username='test_user').first()
        if light_user:
            users_to_test.append(('LIGHT', light_user))

        # Найдем STANDARD пользователя
        standard_user = session.query(User).filter_by(username='test9').first()
        if standard_user:
            users_to_test.append(('STANDARD', standard_user))

        # Найдем PREMIUM пользователя
        premium_user = session.query(User).filter_by(username='test15').first()
        if premium_user:
            users_to_test.append(('PREMIUM', premium_user))

        print("Тестируем доступ к новостям для разных тарифов:")
        print("=" * 50)

        for tier, user in users_to_test:
            print(f"\nТестируем {tier} пользователя: @{user.username}")

            # Вызываем функцию новостей
            try:
                result = await get_news_trends(
                    topic="бизнес",
                    period="week",
                    focus="trends",
                    user_id=user.telegram_id,
                    session=session
                )

                if "доступно только" in result.lower() or "требует" in result.lower() or "подписку" in result.lower():
                    print(f"❌ {tier}: Доступ ограничен - {result[:100]}...")
                else:
                    print(f"✅ {tier}: Доступ разрешен - новости получены")

            except Exception as e:
                print(f"⚠️  {tier}: Ошибка - {str(e)}")

        print("\n" + "=" * 50)
        print("Тест завершен!")

    except Exception as e:
        print(f"Ошибка при тестировании: {e}")
        import traceback
        traceback.print_exc()

    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_news_access())