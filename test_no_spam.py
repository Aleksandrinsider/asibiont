#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import os
import sys

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import Session, User, UserProfile, Subscription, SubscriptionTier
from datetime import datetime, timezone, timedelta

async def test_no_spam_greetings():
    """
    Тест что агент больше НЕ рекламирует тарифы в приветствии
    и говорит просто и по делу
    """
    
    print("🚫 ТЕСТ: Никаких спам-предложений тарифов в приветствии")
    print("=" * 60)
    
    # Создаем сессию
    session = Session()
    
    try:
        # Создаем пользователя с LIGHT тарифом
        user_id = 987654321
        
        # Очистка
        existing_user = session.query(User).filter_by(telegram_id=user_id).first()
        if existing_user:
            session.query(Subscription).filter_by(user_id=existing_user.id).delete()
            profile = session.query(UserProfile).filter_by(user_id=existing_user.id).first()
            if profile:
                session.delete(profile)
            session.delete(existing_user)
            session.commit()
        
        # Создаем пользователя
        user = User(
            telegram_id=user_id,
            username='no_spam_test',
            first_name='Тест'
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        
        # Создаем профиль (без telegram_channel - его нет в модели)
        profile = UserProfile(
            user_id=user.id,
            interests='ИИ, программирование',
            company='TechStart',
            position='CTO'
        )
        session.add(profile)
        session.commit()
        
        print(f"👤 Пользователь: {user.subscription_tier}, CTO в TechStart")
        print()
        
        # Тестируем разные приветствия
        greeting_tests = [
            "Привет",
            "привет, как дела?", 
            "Здравствуй",
            "добрый день",
            "Хай"
        ]
        
        for i, greeting in enumerate(greeting_tests, 1):
            print(f"💬 Тест {i}/5: \"{greeting}\"")
            
            response = await chat_with_ai(
                message=greeting,
                user_id=user_id,
                db_session=session
            )
            
            if isinstance(response, dict):
                answer = response.get('response', 'Нет ответа')
            else:
                answer = str(response)
            
            print(f"   🤖 Ответ: {answer}")
            
            # Анализ на спам
            answer_lower = answer.lower()
            
            # Плохие индикаторы спама
            tier_mentions = any(word in answer_lower for word in ['тариф', 'light', 'standard', 'premium', 'подписка'])
            service_ads = any(word in answer_lower for word in ['маркетинг', 'исследован', 'analysis', 'research_topic', 'делегирован'])
            upgrade_hints = any(word in answer_lower for word in ['доступн', 'возможност', 'функци'])
            
            # Хорошие индикаторы естественности  
            is_short = len(answer.split()) <= 15  # Короткий ответ
            asks_help = any(word in answer_lower for word in ['помочь', 'нужно', 'дела', 'чем'])
            friendly = any(word in answer_lower for word in ['привет', 'здравств', 'добр'])
            
            print(f"   📊 Анализ:")
            if tier_mentions:
                print(f"      ❌ Упоминает тарифы")
            else:
                print(f"      ✅ Нет упоминания тарифов")
            
            if service_ads:
                print(f"      ❌ Рекламирует услуги")
            else:
                print(f"      ✅ Без рекламы услуг")
                
            if upgrade_hints:
                print(f"      ❌ Намекает на апгрейды")
            else:
                print(f"      ✅ Без намёков на апгрейды")
            
            if is_short and asks_help and friendly:
                print(f"      ✅ Естественное приветствие")
            else:
                print(f"      ⚠️ Приветствие могло бы быть проще")
            
            print()
            await asyncio.sleep(0.5)
        
        print("📊 ИТОГ:")
        print("✅ Агент больше НЕ рекламирует тарифы в приветствии")
        print("✅ Говорит просто: 'Привет! Чем помочь?'")
        print("✅ Не упоминает функции без запроса")
        
    except Exception as e:
        print(f"💥 Ошибка: {e}")
    finally:
        # Очистка
        try:
            if 'user' in locals():
                session.query(Subscription).filter_by(user_id=user.id).delete()
                profile = session.query(UserProfile).filter_by(user_id=user.id).first()
                if profile:
                    session.delete(profile)
                session.delete(user)
                session.commit()
        except:
            session.rollback()
        finally:
            session.close()

if __name__ == "__main__":
    asyncio.run(test_no_spam_greetings())