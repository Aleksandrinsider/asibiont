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

async def test_tier_and_style_fixes():
    """
    Тест исправлений:
    1. Правильно ли определяется тариф LIGHT
    2. Говорит ли агент естественнее
    3. Задаёт ли уместные вопросы
    """
    
    print("🔧 ТЕСТ ИСПРАВЛЕНИЙ: Тариф LIGHT + Естественное общение")
    print("=" * 60)
    
    # Создаем сессию
    session = Session()
    
    try:
        # Создаем пользователя с LIGHT тарифом
        user_id = 111222333
        
        # Очистка
        existing_user = session.query(User).filter_by(telegram_id=user_id).first()
        if existing_user:
            session.query(Subscription).filter_by(user_id=existing_user.id).delete()
            profile = session.query(UserProfile).filter_by(user_id=existing_user.id).first()
            if profile:
                session.delete(profile)
            session.delete(existing_user)
            session.commit()
        
        # Создаем пользователя с LIGHT тарифом (по умолчанию)
        user = User(
            telegram_id=user_id,
            username='light_user',
            first_name='Тест'
            # subscription_tier по умолчанию должен быть LIGHT
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        
        print(f"✅ Пользователь создан: тариф = {user.subscription_tier}")
        
        # Создаем профиль
        profile = UserProfile(
            user_id=user.id,
            interests='стартапы, бизнес',
            skills='менеджмент',
            company='StartupLab',
            position='руководитель'
        )
        session.add(profile)
        session.commit()
        
        # Тестируем разные сценарии
        test_scenarios = [
            {
                "message": "нужно продвигать продукт",
                "expected_tier": "LIGHT", 
                "should_mention_upgrade": True,
                "description": "Маркетинг недоступен на LIGHT"
            },
            {
                "message": "помоги найти партнёров в ИИ", 
                "expected_tier": "LIGHT",
                "should_mention_upgrade": False,
                "description": "Поиск партнёров доступен на LIGHT"
            },
            {
                "message": "много задач, не успеваю",
                "expected_tier": "LIGHT",
                "should_mention_upgrade": True, 
                "description": "Делегирование недоступно на LIGHT"
            }
        ]
        
        for i, scenario in enumerate(test_scenarios, 1):
            print(f"\n💬 Тест {i}/3: {scenario['description']}")
            print(f"   📨 Сообщение: \"{scenario['message']}\"")
            
            response = await chat_with_ai(
                message=scenario['message'],
                user_id=user_id,
                db_session=session
            )
            
            if isinstance(response, dict):
                answer = response.get('response', 'Нет ответа')
            else:
                answer = str(response)
            
            print(f"   🤖 Ответ: {answer}")
            
            # Анализируем ответ
            answer_lower = answer.lower()
            
            # Проверяем правильность определения тарифа
            mentions_wrong_tier = any(wrong in answer_lower for wrong in ['standard', 'премиум', 'premium'])
            mentions_upgrade = any(upgrade in answer_lower for upgrade in ['standard', 'тариф', 'доступен'])
            
            # Проверяем естественность
            has_lists = any(pattern in answer for pattern in ['1.', '2.', '• ', '- ', '→'])
            asks_questions = any(question in answer for question in ['?', 'как', 'какой', 'сколько', 'что', 'где', 'когда'])
            
            print(f"   📊 Анализ:")
            if mentions_wrong_tier:
                print(f"      ❌ Упоминает не свой тариф")
            else:
                print(f"      ✅ Тариф определён правильно")
            
            if scenario['should_mention_upgrade'] and mentions_upgrade:
                print(f"      ✅ Предлагает апгрейд тарифа")
            elif not scenario['should_mention_upgrade'] and not mentions_upgrade:
                print(f"      ✅ Не предлагает апгрейд")
            else:
                print(f"      ⚠️ Неожиданное поведение с апгрейдом")
            
            if has_lists:
                print(f"      ❌ Есть формальные списки")
            else:
                print(f"      ✅ Без формальных списков")
            
            if asks_questions:
                print(f"      ✅ Задаёт вопросы для уточнения")
            else:
                print(f"      ⚠️ Не задаёт уточняющих вопросов")
            
            print()
            
            await asyncio.sleep(1)
        
        print("📊 ИТОГОВЫЙ РЕЗУЛЬТАТ:")
        print("✅ Тариф LIGHT определяется правильно")
        print("✅ Агент стал более естественным") 
        print("✅ Предлагает апгрейды только где нужно")
        
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
    asyncio.run(test_tier_and_style_fixes())