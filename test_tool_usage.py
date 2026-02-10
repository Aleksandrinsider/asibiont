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

async def test_tool_usage():
    """
    Диагностический тест: почему агент не использует инструменты
    """
    
    print("🔍 ДИАГНОСТИКА: Использование инструментов агентом")
    print("=" * 60)
    
    # Создаем сессию для теста
    session = Session()
    
    try:
        # Создаем тестового пользователя с STANDARD подпиской
        user_id = 999888777  # Уникальный ID для диагностики
        
        # Очистка если есть
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
            username='test_diag',
            first_name='Диагностик',
            subscription_tier=SubscriptionTier.STANDARD
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        
        # Создаем подписку
        subscription = Subscription(
            user_id=user.id,
            telegram_id=user_id,
            telegram_username=user.username,
            username=user.username,
            plan='STANDARD',
            tier=SubscriptionTier.STANDARD,
            status='active',
            start_date=datetime.now(timezone.utc),
            end_date=datetime.now(timezone.utc) + timedelta(days=30)
        )
        session.add(subscription)
        session.commit()
        
        # Создаем профиль
        profile = UserProfile(
            user_id=user.id,
            interests='ИИ, тестирование',
            skills='программирование'
        )
        session.add(profile)
        session.commit()
        
        print(f"✅ Создан пользователь с STANDARD подпиской")
        print(f"   User ID: {user.id}, Telegram ID: {user_id}")
        print(f"   Тариф: {user.subscription_tier}")
        print()
        
        # Тестовые сообщения, которые должны вызывать инструменты
        test_cases = [
            {
                "message": "создай задачу 'Протестировать агента' на сегодня в 18:00",
                "expected_tools": ["add_task"],
                "description": "Создание задачи"
            },
            {
                "message": "найди партнеров по ИИ",
                "expected_tools": ["find_partners"],
                "description": "Поиск партнеров"
            },
            {
                "message": "исследуй тему 'AI агенты в бизнесе'",
                "expected_tools": ["research_topic"],
                "description": "Исследование темы (STANDARD)"
            },
            {
                "message": "покажи мои задачи",
                "expected_tools": ["list_tasks"],
                "description": "Список задач"
            }
        ]
        
        for i, test_case in enumerate(test_cases, 1):
            print(f"🧪 ТЕСТ {i}: {test_case['description']}")
            print(f"   Сообщение: \"{test_case['message']}\"")
            print(f"   Ожидаемые инструменты: {test_case['expected_tools']}")
            
            try:
                # Вызываем агента
                response = await chat_with_ai(
                    message=test_case['message'],
                    user_id=user_id,
                    db_session=session
                )
                
                # Анализируем ответ
                if isinstance(response, dict):
                    response_text = response.get('response', 'Нет ответа')
                    used_tools = response.get('tools_used', [])
                else:
                    response_text = str(response)
                    used_tools = []
                
                print(f"   🤖 Ответ: {response_text[:100]}...")
                
                if used_tools:
                    print(f"   ✅ Использованы инструменты: {used_tools}")
                    # Проверяем совпадение
                    expected = set(test_case['expected_tools'])
                    actual = set(used_tools)
                    if expected.intersection(actual):
                        print(f"   🎯 УСПЕХ: Ожидаемые инструменты найдены")
                    else:
                        print(f"   ⚠️ ЧАСТИЧНО: Инструменты есть, но не те что ожидались")
                else:
                    print(f"   ❌ ПРОБЛЕМА: Инструменты НЕ ИСПОЛЬЗОВАНЫ")
                
            except Exception as e:
                print(f"   💥 ОШИБКА: {e}")
            
            print()
            await asyncio.sleep(1)  # Пауза между тестами
        
    except Exception as e:
        print(f"💥 Критическая ошибка: {e}")
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
    
    print("🔍 ДИАГНОСТИКА ЗАВЕРШЕНА")

if __name__ == "__main__":
    asyncio.run(test_tool_usage())