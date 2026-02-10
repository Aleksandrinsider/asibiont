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

async def test_conversation_style():
    """
    Тест стиля общения: проверяем что агент говорит как живой человек,
    без формальных списков и структур
    """
    
    print("💬 ТЕСТ СТИЛЯ ОБЩЕНИЯ: Живой разговор vs Формальные списки")
    print("=" * 70)
    
    # Создаем сессию для теста
    session = Session()
    
    try:
        # Создаем тестового пользователя с STANDARD подпиской
        user_id = 555666777  # Уникальный ID для теста стиля
        
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
            username='style_test',
            first_name='Человек',
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
            interests='стартапы, маркетинг',
            skills='продажи',
            company='MyStartup',
            position='основатель'
        )
        session.add(profile)
        session.commit()
        
        print(f"✅ Настроен пользователь-основатель стартапа")
        print()
        
        # Тестовые сценарии для проверки стиля
        style_tests = [
            {
                "message": "нужно привлечь клиентов для моего продукта",
                "bad_patterns": [
                    "1.", "2.", "3.",  # Нумерованные списки
                    "**Вариант", "**Быстрый старт**:", "**Системный",  # Формальные структуры
                    "Какой вариант", "что больше подходит", "что выбираешь"  # Лишние вопросы
                ],
                "good_patterns": [
                    "найд", "создать", "помочь", "могу"  # Действия вместо вариантов
                ],
                "description": "Совет по привлечению клиентов"
            },
            {
                "message": "как лучше продвигать стартап?",
                "bad_patterns": [
                    "1.", "2.", "→", "**Первый способ**", "Есть несколько вариантов"
                ],
                "good_patterns": [
                    "лучше", "начни", "попробуй", "поможет"
                ],
                "description": "Совет по продвижению"
            },
            {
                "message": "что мне делать для роста бизнеса?",
                "bad_patterns": [
                    "• ", "- ", "→", "варианта:", "подходы:"
                ],
                "good_patterns": [
                    "сначала", "потом", "затем", "также"
                ],
                "description": "План действий для роста"
            }
        ]
        
        # Проводим тесты
        total_score = 0
        max_possible = 0
        
        for i, test in enumerate(style_tests, 1):
            print(f"💬 Тест {i}: {test['description']}")
            print(f"   📨 Сообщение: \"{test['message']}\"")
            
            try:
                # Получаем ответ от агента
                response = await chat_with_ai(
                    message=test['message'],
                    user_id=user_id,
                    db_session=session
                )
                
                if isinstance(response, dict):
                    response_text = response.get('response', 'Нет ответа')
                else:
                    response_text = str(response)
                
                print(f"   🤖 Ответ: {response_text}")
                print()
                
                # Анализируем стиль
                style_score = 0
                max_test_score = 0
                
                # Проверяем на плохие паттерны (формализм)
                bad_found = []
                for pattern in test['bad_patterns']:
                    if pattern.lower() in response_text.lower():
                        bad_found.append(pattern)
                
                # Проверяем на хорошие паттерны (живое общение)
                good_found = []
                for pattern in test['good_patterns']:
                    if pattern.lower() in response_text.lower():
                        good_found.append(pattern)
                
                # Оценка стиля
                max_test_score += 10  # За отсутствие плохих паттернов
                if not bad_found:
                    style_score += 10
                    print(f"   ✅ Нет формальных структур (+10)")
                else:
                    penalty = len(bad_found) * 2
                    style_score += max(0, 10 - penalty)
                    print(f"   ❌ Найдены формальные элементы: {bad_found} (-{penalty})")
                
                max_test_score += 5  # За живую речь
                if good_found:
                    style_score += 5
                    print(f"   ✅ Живая речь: {good_found} (+5)")
                else:
                    print(f"   ⚠️ Недостаточно живости в ответе")
                
                # Проверка длины - живое общение должно быть кратким
                lines = [line for line in response_text.split('\n') if line.strip()]
                max_test_score += 5
                if len(lines) <= 4:
                    style_score += 5
                    print(f"   ✅ Краткость: {len(lines)} строк (+5)")
                else:
                    print(f"   ⚠️ Многословно: {len(lines)} строк")
                
                total_score += style_score
                max_possible += max_test_score
                
                print(f"   📊 Оценка стиля: {style_score}/{max_test_score}")
                print()
                
            except Exception as e:
                print(f"   💥 Ошибка: {e}")
                print()
            
            await asyncio.sleep(1)
        
        # Итоговая оценка
        print("=" * 70)
        print("📊 ИТОГОВАЯ ОЦЕНКА СТИЛЯ ОБЩЕНИЯ:")
        print(f"   Набрано баллов: {total_score}/{max_possible}")
        
        percentage = (total_score / max_possible * 100) if max_possible > 0 else 0
        
        if percentage >= 90:
            rating = "🏆 ОТЛИЧНО - Говорит как живой человек!"
        elif percentage >= 75:
            rating = "✅ ХОРОШО - В основном естественно"
        elif percentage >= 60:
            rating = "⚠️ СРЕДНЕ - Есть элементы формализма"
        else:
            rating = "❌ ПЛОХО - Слишком формальный стиль"
        
        print(f"   Процент качества: {percentage:.1f}%")
        print(f"   {rating}")
        
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
    
    print("\n💬 ТЕСТ СТИЛЯ ЗАВЕРШЁН")

if __name__ == "__main__":
    asyncio.run(test_conversation_style())