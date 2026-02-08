"""
Тест реального диалога из примера пользователя
"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.autonomous_agent import chat_with_ai
from models import Session, User, UserProfile, Task, SubscriptionTier

async def test_real_scenario():
    """Тестируем точный сценарий из проблемы пользователя"""
    
    test_user_id = 888999777
    session = Session()
    
    print("\n" + "="*80)
    print("ТЕСТ: Сценарий из проблемы пользователя")
    print("="*80 + "\n")
    
    try:
        # Очистка
        session.query(Task).filter_by(user_id=session.query(User).filter_by(telegram_id=test_user_id).first().id if session.query(User).filter_by(telegram_id=test_user_id).first() else None).delete()
        session.query(UserProfile).filter_by(user_id=session.query(User).filter_by(telegram_id=test_user_id).first().id if session.query(User).filter_by(telegram_id=test_user_id).first() else None).delete()
        session.query(User).filter_by(telegram_id=test_user_id).delete()
        session.commit()
        
        # Создание пользователя
        user =User(
            telegram_id=test_user_id,
            username="test_real_user",
            first_name="Александр",
            subscription_tier=SubscriptionTier.PREMIUM
        )
        session.add(user)
        session.commit()
        
        # Профиль
        profile = UserProfile(
            user_id=user.id,
            city="Пермь",
            company="ASI Biont",
            interests="бизнес, ИИ, компьютерные игры, книги ЛитРПГ"
        )
        session.add(profile)
        session.commit()
        
        print("[1/5] Пользователь: 'Привет'")
        r1 = await chat_with_ai("Привет", user_id=test_user_id, db_session=session)
        print(f"AI: {r1['response'][:150]}...\n")
        await asyncio.sleep(0.5)
        
        print("[2/5] Пользователь: 'Пока нет планов'")
        r2 = await chat_with_ai("Пока нет планов", user_id=test_user_id, db_session=session)
        print(f"AI: {r2['response'][:150]}...\n")
        await asyncio.sleep(0.5)
        
        print("[3/5] Пользователь: 'Хотя есть одна проблема. Я создал бота ии но ни как не могу привлечь пользователей'")
        r3 = await chat_with_ai("Хотя есть одна проблема. Я создал бота ии но ни как не могу привлечь пользователей", user_id=test_user_id, db_session=session)
        print(f"AI: {r3['response'][:250]}...\n")
        await asyncio.sleep(0.5)
        
        print("[4/5] Пользователь: 'Давай с чего то начнем. Что порекомендуешь?'")
        r4 = await chat_with_ai("Давай с чего то начнем. Что порекомендуешь?", user_id=test_user_id, db_session=session)
        print(f"AI: {r4['response'][:250]}...\n")
        await asyncio.sleep(0.5)
        
        print("[5/5] КРИТИЧНО: Пользователь: 'да создай'")
        r5 = await chat_with_ai("да создай", user_id=test_user_id, db_session=session)
        print(f"AI: {r5['response'][:250]}...\n")
        
        # Проверка
        print("\n" + "="*80)
        print("РЕЗУЛЬТАТ")
        print("="*80)
        
        tasks = session.query(Task).filter_by(user_id=user.id).all()
        
        if len(tasks) > 0:
            print(f"✅ AI СОЗДАЛ ЗАДАЧИ: {len(tasks)} шт.")
            for task in tasks:
                print(f"   - '{task.title}'")
            print()
            
            # Проверяем, что не спрашивал повторно
            if "что создать" in r5['response'].lower() or "какую задачу" in r5['response'].lower() or "что именно" in r5['response'].lower():
                print("❌ AI спросил повторно 'что создать' - ПРОБЛЕМА НЕ РЕШЕНА")
            else:
                print("✅ AI НЕ СПРОСИЛ 'что создать' - ПРОБЛЕМА РЕШЕНА!")
                print("✅ AI правильно понял подтверждение из контекста")
        else:
            print("❌ Задачи НЕ созданы")
            
        print("="*80 + "\n")
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_real_scenario())
