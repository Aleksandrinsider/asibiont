"""
Тест для проверки контекстного понимания и обработки подтверждений
"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.autonomous_agent import chat_with_ai
from ai_integration.conversation_history import (
    get_conversation_history, 
    clear_conversation_history
)
from models import Session, User, Task, SubscriptionTier

async def test_confirmation_context():
    """Тест обработки подтверждений с контекстом"""
    
    test_user_id = 999888777
    session = Session()
    
    print("\n" + "="*80)
    print("ТЕСТ: Обработка подтверждений с контекстом диалога")
    print("="*80 + "\n")
    
    try:
        # Очистка тестового пользователя
        session.query(Task).filter_by(user_id=session.query(User).filter_by(telegram_id=test_user_id).first().id if session.query(User).filter_by(telegram_id=test_user_id).first() else None).delete()
        session.query(User).filter_by(telegram_id=test_user_id).delete()
        session.commit()
        
        # Создание тестового пользователя
        user = User(
            telegram_id=test_user_id,
            username="test_confirmation_user",
            first_name="Тест",
            subscription_tier=SubscriptionTier.PREMIUM
        )
        session.add(user)
        session.commit()
        
        # Очищаем историю
        clear_conversation_history(test_user_id, session)
        
        print("[ИСТОРИЯ ДО ТЕСТА]")
        history = get_conversation_history(test_user_id, session)
        print(f"Сообщений в истории: {len(history)}\n")
        
        # Сообщение 1: AI предлагает создать задачу
        print("[1/3] Пользователь: 'Есть проблема: не могу привлечь пользователей для бота'")
        result1 = await chat_with_ai(
            message="Есть проблема: не могу привлечь пользователей для бота",
            user_id=test_user_id,
            db_session=session
        )
        print(f"Ответ AI: {result1['response'][:200]}...\n")
        
        await asyncio.sleep(1)
        
        # Сообщение 2: AI должен конкретизировать
        print("[2/3] Пользователь: 'Давай с чего-то начнем'")
        result2 = await chat_with_ai(
            message="Давай с чего-то начнем",
            user_id=test_user_id,
            db_session=session
        )
        print(f"Ответ AI: {result2['response'][:300]}...\n")
        
        await asyncio.sleep(1)
        
        # Проверяем, что AI предложил конкретную задачу
        if "создам" in result2['response'].lower() or "задач" in result2['response'].lower():
            print("✅ AI предложил создать задачу\n")
        
        # Сообщение 3: КРИТИЧНЫЙ МОМЕНТ - подтверждение
        print("[3/3] Пользователь: 'да создай'")
        result3 = await chat_with_ai(
            message="да создай",
            user_id=test_user_id,
            db_session=session
        )
        print(f"Ответ AI: {result3['response'][:300]}...\n")
        
        # Проверка истории
        print("[ИСТОРИЯ ПОСЛЕ ТЕСТА]")
        history_after = get_conversation_history(test_user_id, session)
        print(f"Сообщений в истории: {len(history_after)}")
        for i, msg in enumerate(history_after[-6:], 1):
            print(f"  {i}. {msg['role']}: {msg['content'][:80]}...")
        print()
        
        # Проверка результата
        print("\n" + "="*80)
        print("РЕЗУЛЬТАТЫ ПРОВЕРКИ")
        print("="*80)
        
        tasks = session.query(Task).filter_by(user_id=user.id).all()
        print(f"✅ История диалога: {len(history_after)} сообщений (ожидается 6)")
        print(f"{'✅' if len(tasks) > 0 else '❌'} Задачи созданы: {len(tasks)}")
        
        if len(tasks) > 0:
            for task in tasks:
                print(f"   - '{task.title}'")
        
        # Проверка понимания подтверждения
        confirmation_understood = False
        if len(tasks) > 0:
            confirmation_understood = True
            print("✅ AI правильно понял подтверждение 'да создай'")
        elif "что создать" in result3['response'].lower() or "какую задачу" in result3['response'].lower():
            print("❌ AI НЕ ПОНЯЛ подтверждение - спрашивает повторно")
        else:
            print(f"⚠️  Неясный результат: {result3['response'][:150]}")
        
        print("\n" + "="*80)
        if confirmation_understood and len(history_after) >= 6:
            print("✅ ТЕСТ ПРОЙДЕН: История и контекст работают")
        else:
            print("❌ ТЕСТ ПРОВАЛЕН: Требуется доработка")
        print("="*80 + "\n")
        
    except Exception as e:
        print(f"\n❌ Ошибка теста: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_confirmation_context())
