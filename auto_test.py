"""
Автоматический тест AI агента
"""
import asyncio
import sys
from ai_integration import chat_with_ai
from models import SessionLocal, User, Task, UserProfile
from datetime import datetime
import pytz

USER_ID = 146333757

async def test_agent():
    """Автоматический тест работы агента"""
    
    print("=" * 70)
    print("АВТОМАТИЧЕСКИЙ ТЕСТ AI АГЕНТА")
    print("=" * 70)
    
    session = SessionLocal()
    
    # Создать пользователя если нет
    user = session.query(User).filter_by(telegram_id=USER_ID).first()
    if not user:
        user = User(
            telegram_id=USER_ID,
            username="testuser",
            first_name="Test",
            timezone="Europe/Moscow"
        )
        session.add(user)
        session.commit()
        print(f"✅ Создан пользователь {USER_ID}")
    
    # Проверить профиль
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if not profile:
        profile = UserProfile(
            user_id=user.id,
            city="Moscow",
            skills="Python, AI",
            interests="Tech, Programming"
        )
        session.add(profile)
        session.commit()
        print("✅ Создан профиль")
    
    # Очистить старые задачи
    old_tasks = session.query(Task).filter_by(user_id=user.id).all()
    for t in old_tasks:
        session.delete(t)
    session.commit()
    print(f"✅ Очищено {len(old_tasks)} старых задач")
    
    # Тестовые сценарии
    test_cases = [
        {
            "name": "Список задач (пустой)",
            "message": "Покажи мои задачи",
            "expected": "задач нет"
        },
        {
            "name": "Добавление конкретной задачи",
            "message": "Добавь задачу: Отправить отчет директору на завтра 14:00",
            "expected": "добавил"
        },
        {
            "name": "Список задач (1 задача)",
            "message": "Покажи задачи",
            "expected": "отправить отчет"
        },
        {
            "name": "Общая задача - требует уточнения",
            "message": "Добавь задачу проверить почту",
            "expected": "письме"  # AI должен спросить о каком письме
        },
    ]
    
    print("\n" + "=" * 70)
    print("ЗАПУСК ТЕСТОВ")
    print("=" * 70 + "\n")
    
    passed = 0
    failed = 0
    
    for i, test in enumerate(test_cases, 1):
        print(f"\n[ТЕСТ {i}] {test['name']}")
        print(f"Сообщение: '{test['message']}'")
        
        try:
            # Вызов AI
            response = await asyncio.wait_for(
                chat_with_ai(message=test['message'], user_id=USER_ID),
                timeout=60.0
            )
            
            print(f"Ответ AI: {response[:200]}...")
            
            # Проверка результата
            if test['expected'].lower() in response.lower():
                print("✅ PASSED")
                passed += 1
            else:
                print(f"❌ FAILED - ожидалось '{test['expected']}' в ответе")
                failed += 1
            
            # Показать состояние БД
            session.expire_all()
            tasks = session.query(Task).filter_by(user_id=user.id).all()
            print(f"Задач в БД: {len(tasks)}")
            
        except asyncio.TimeoutError:
            print("❌ TIMEOUT")
            failed += 1
        except Exception as e:
            print(f"❌ ERROR: {e}")
            failed += 1
        
        await asyncio.sleep(1)  # Пауза между тестами
    
    # Итоги
    print("\n" + "=" * 70)
    print("РЕЗУЛЬТАТЫ")
    print("=" * 70)
    print(f"✅ Пройдено: {passed}/{len(test_cases)}")
    print(f"❌ Провалено: {failed}/{len(test_cases)}")
    
    # Финальная проверка БД
    session.expire_all()
    final_tasks = session.query(Task).filter_by(user_id=user.id).all()
    print(f"\nЗадач в БД после тестов: {len(final_tasks)}")
    for task in final_tasks:
        tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
        time_str = task.reminder_time.astimezone(tz).strftime('%Y-%m-%d %H:%M') if task.reminder_time else 'нет времени'
        print(f"  - {task.title} ({time_str})")
    
    session.close()
    
    print("\n" + "=" * 70)
    if failed == 0:
        print("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ!")
        sys.exit(0)
    else:
        print(f"⚠️ ЕСТЬ ОШИБКИ: {failed} тестов провалено")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(test_agent())
