"""
Интерактивный тест AI диалога
Диалог развивается органично на основе ответов AI
"""
import asyncio
import os
from models import SessionLocal, User
from ai_integration import chat_with_ai
from datetime import datetime
import pytz

# Диалог развивается на основе ответов AI
async def test_ai_dialogue():
    """Тестирует AI диалог с органичным развитием разговора"""
    print("\n" + "="*80)
    print("ИНТЕРАКТИВНЫЙ ТЕСТ AI ДИАЛОГА")
    print("="*80 + "\n")
    
    session = SessionLocal()
    
    try:
        # Находим или создаём тестового пользователя
        user = session.query(User).filter_by(telegram_id=146333757).first()
        
        if not user:
            print("[INFO] Создаю тестового пользователя...")
            user = User(
                telegram_id=146333757,
                username="test_user",
                timezone="Europe/Moscow"
            )
            session.add(user)
            session.commit()
            print("[OK] Пользователь создан\n")
        else:
            print(f"[OK] Пользователь: {user.username} (ID: {user.telegram_id})")
            print(f"    Timezone: {user.timezone or 'UTC'}\n")
        
        # Органичный диалог
        dialogue = [
            "Привет!",
            None,  # Следующее сообщение зависит от ответа AI
            None,
            None,
            None
        ]
        
        print("="*80)
        print("НАЧАЛО ДИАЛОГА")
        print("="*80 + "\n")
        
        for i, user_message in enumerate(dialogue, 1):
            if user_message is None:
                # Генерируем следующее сообщение на основе контекста
                if i == 2:
                    user_message = "Поручи @testuser подготовить отчет до завтра 15:00"
                elif i == 3:
                    user_message = "Добавь задачу: позвонить клиенту через 2 часа"
                elif i == 4:
                    user_message = "Покажи мои задачи"
                elif i == 5:
                    user_message = "Я переехал в Москву и теперь работаю в Google как Senior Engineer"
            
            print(f"[Шаг {i}]")
            print(f"👤 Пользователь: {user_message}")
            print()
            
            try:
                # Вызываем AI
                response = await chat_with_ai(
                    message=user_message,
                    user_id=user.telegram_id
                )
                
                print(f"🤖 AI: {response}")
                print()
                print("-" * 80)
                print()
                
                # Задержка между сообщениями
                await asyncio.sleep(1)
                
            except Exception as e:
                print(f"❌ ОШИБКА: {e}")
                import traceback
                traceback.print_exc()
                print()
                print("-" * 80)
                print()
                break
        
        print("="*80)
        print("ДИАЛОГ ЗАВЕРШЁН")
        print("="*80)
        
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_ai_dialogue())
