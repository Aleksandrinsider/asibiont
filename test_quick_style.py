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

async def quick_style_test():
    """
    Быстрый тест - один простой вопрос чтобы убедиться что
    агент говорит естественно без списков
    """
    
    print("⚡ БЫСТРЫЙ ТЕСТ СТИЛЯ: Естественное общение")
    print("=" * 50)
    
    # Создаем сессию для теста
    session = Session()
    
    try:
        # Создаем тестового пользователя с STANDARD подпиской
        user_id = 999999999  # Уникальный ID для быстрого теста
        
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
            username='quick_test',
            first_name='Тестер',
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
            interests='аналитика, рост',
            skills='данные',
            company='TestCompany',
            position='аналитик'
        )
        session.add(profile)
        session.commit()
        
        print("💼 Вопрос: Как увеличить конверсию на сайте?")
        print()
        
        # Получаем ответ от агента
        response = await chat_with_ai(
            message="как увеличить конверсию на сайте?",
            user_id=user_id,
            db_session=session
        )
        
        if isinstance(response, dict):
            response_text = response.get('response', 'Нет ответа')
        else:
            response_text = str(response)
        
        print("🤖 ОТВЕТ АГЕНТА:")
        print(response_text)
        print()
        
        # Анализ стиля
        print("🔍 АНАЛИЗ СТИЛЯ:")
        
        # Плохие паттерны (формальность)
        bad_patterns = [
            "1.", "2.", "3.", "4.", "5.",  # Нумерованные списки
            "• ", "- ", "→",  # Маркированные списки
            "**Вариант", "**Первый", "**Второй",  # Формальные структуры
            "варианта:", "подходы:", "способы:",  # Формальные заголовки
        ]
        
        # Хорошие паттерны (живое общение)  
        good_patterns = [
            "попроб", "сначала", "потом", "лучше",
            "могу", "поможет", "давай", "хочешь"
        ]
        
        # Проверяем
        bad_found = [pattern for pattern in bad_patterns if pattern in response_text]
        good_found = [pattern for pattern in good_patterns if pattern.lower() in response_text.lower()]
        
        if bad_found:
            print(f"❌ НАЙДЕНЫ ФОРМАЛЬНЫЕ ЭЛЕМЕНТЫ: {bad_found}")
        else:
            print("✅ НЕТ ФОРМАЛЬНЫХ СТРУКТУР - отлично!")
        
        if good_found:
            print(f"✅ ЖИВАЯ РЕЧЬ: {good_found}")
        else:
            print("⚠️ Мало живости в речи")
        
        # Подсчёт строк
        lines = [line for line in response_text.split('\n') if line.strip()]
        print(f"📏 КРАТКОСТЬ: {len(lines)} строк")
        
        # Общая оценка
        if not bad_found and good_found and len(lines) <= 5:
            print("🏆 РЕЗУЛЬТАТ: Отлично! Говорит как живой человек")
        elif not bad_found and len(lines) <= 5:
            print("✅ РЕЗУЛЬТАТ: Хорошо! Нет формализма, но мало живости")
        elif not bad_found:
            print("⚠️ РЕЗУЛЬТАТ: Средне. Нет формализма, но многословно")
        else:
            print("❌ РЕЗУЛЬТАТ: Плохо. Остался формальный стиль")
        
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
    asyncio.run(quick_style_test())