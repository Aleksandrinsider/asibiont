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

async def final_comparison_test():
    """
    Финальный тест: показываем ДО и ПОСЛЕ изменений стиля
    """
    
    print("🎯 ИТОГОВЫЙ ТЕСТ: Естественное общение БЕЗ СПИСКОВ")
    print("=" * 60)
    
    # Создаем сессию
    session = Session()
    
    try:
        # Создаем пользователя
        user_id = 777888999
        
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
            username='final_test',
            first_name='Финал',
            subscription_tier=SubscriptionTier.STANDARD
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        
        # Подписка
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
        
        # Профиль
        profile = UserProfile(
            user_id=user.id,
            interests='продажи, маркетинг',
            skills='переговоры',
            company='SalesBoost',
            position='менеджер'
        )
        session.add(profile)
        session.commit()
        
        print("👤 Пользователь: Менеджер по продажам")
        print("💬 Вопрос: «как увеличить продажи?»")
        print()
        
        # Быстрый тест
        response = await chat_with_ai(
            message="как увеличить продажи?",
            user_id=user_id,
            db_session=session
        )
        
        if isinstance(response, dict):
            answer = response.get('response', 'Нет ответа')
        else:
            answer = str(response)
        
        print("🤖 ОТВЕТ АГЕНТА:")
        print(f"   {answer}")
        print()
        
        # Быстрая проверка
        has_lists = any(pattern in answer for pattern in ["1.", "2.", "3.", "• ", "- ", "→"])
        has_formal = any(pattern in answer for pattern in ["**Вариант", "способ:", "варианта:"])
        has_live = any(pattern in answer.lower() for pattern in ["попроб", "лучше", "сначала", "могу"])
        is_short = len([line for line in answer.split('\n') if line.strip()]) <= 4
        
        print("📊 АНАЛИЗ РЕЗУЛЬТАТА:")
        if has_lists:
            print("❌ Есть нумерованные/маркированные списки")
        else:
            print("✅ Нет формальных списков")
        
        if has_formal:
            print("❌ Есть формальные структуры")
        else:
            print("✅ Нет формальных структур")
        
        if has_live:
            print("✅ Живая, естественная речь")
        else:
            print("⚠️ Мало живости в речи")
        
        if is_short:
            print("✅ Краткий ответ")
        else:
            print("⚠️ Ответ длинный")
        
        print()
        
        # Общий результат
        success_count = sum([not has_lists, not has_formal, has_live, is_short])
        
        if success_count >= 3:
            print("🏆 УСПЕХ: Агент говорит естественно, как живой человек!")
        elif success_count >= 2:
            print("✅ ХОРОШО: Большие улучшения в стиле общения")
        else:
            print("⚠️ СРЕДНЕ: Частичные улучшения")
        
        print(f"📈 Оценка: {success_count}/4 критерия выполнены")
        
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
    asyncio.run(final_comparison_test())