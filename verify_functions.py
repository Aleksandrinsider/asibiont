"""Быстрая проверка работы всех функций после оптимизации"""
import asyncio
from models import Session, User, UserProfile, Task
from ai_integration.chat import chat_with_ai
from datetime import datetime

async def verify():
    session = Session()
    user_id = 777666555
    
    # Очистка
    session.query(Task).delete()
    session.query(User).filter_by(telegram_id=user_id).delete()
    session.commit()
    
    # Создание пользователя
    user = User(telegram_id=user_id, username="test_user")
    session.add(user)
    session.commit()
    
    print("\n══════════════════════════════════════════")
    print("ПРОВЕРКА ПОСЛЕ ОПТИМИЗАЦИИ (933→181 строк)")
    print("══════════════════════════════════════════\n")
    
    tests_passed = 0
    total_tests = 5
    
    # 1. Создание задачи
    print("[1/5] Создание задачи...")
    try:
        result = await chat_with_ai("создай задачу позвонить начальнику завтра в 15:00", user_id=user_id, db_session=session)
        response = result.get('response', '') if isinstance(result, dict) else result
        task = session.query(Task).first()
        if task and "позвонить" in task.title.lower():
            print(f"✅ Задача создана: '{task.title}'")
            if task.reminder_time:
                print(f"   Время: {task.reminder_time.strftime('%d.%m %H:%M')}")
            tests_passed += 1
        else:
            print("❌ Задача не создана")
    except Exception as e:
        print(f"❌ Ошибка: {str(e)[:100]}")
    
    # 2. Просмотр задач
    print("\n[2/5] Просмотр задач...")
    try:
        result = await chat_with_ai("покажи мои задачи", user_id=user_id, db_session=session)
        response = result.get('response', '') if isinstance(result, dict) else result
        if response and ("позвонить" in response.lower() or "задач" in response.lower()):
            print(f"✅ Список показан (длина: {len(response)} символов)")
            tests_passed += 1
        else:
            print("❌ Список не показан")
    except Exception as e:
        print(f"❌ Ошибка: {str(e)[:100]}")
    
    # 3. Обновление профиля
    print("\n[3/5] Обновление профиля...")
    try:
        result = await chat_with_ai("я работаю в компании StartupX и умею Python", user_id=user_id, db_session=session)
        session.refresh(user)
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile and "startupx" in (profile.company or "").lower():
            print(f"✅ Компания: {profile.company}")
            print(f"   Навыки: {profile.skills}")
            tests_passed += 1
        else:
            print(f"❌ Профиль не обновлён: {profile.company if profile else 'Нет профиля'}")
    except Exception as e:
        print(f"❌ Ошибка: {str(e)[:100]}")
    
    # 4. Использование контекста
    print("\n[4/5] Использование контекста...")
    try:
        result = await chat_with_ai("дай совет по карьере", user_id=user_id, db_session=session)
        response = result.get('response', '') if isinstance(result, dict) else result
        if response and ("startupx" in response.lower() or "python" in response.lower()):
            print(f"✅ Контекст использован (упомянул компанию/навыки)")
            print(f"   Фрагмент: {response[:120]}...")
            tests_passed += 1
        else:
            print("❌ Контекст не использован")
            print(f"   Ответ: {response[:120]}...")
    except Exception as e:
        print(f"❌ Ошибка: {str(e)[:100]}")
    
    # 5. Завершение задачи
    print("\n[5/5] Завершение задачи...")
    try:
        result = await chat_with_ai("я позвонил начальнику, задача выполнена", user_id=user_id, db_session=session)
        task = session.query(Task).filter_by(user_id=user_id).first()
        if task and task.is_completed:
            print(f"✅ Задача завершена: '{task.title}'")
            tests_passed += 1
        else:
            print("❌ Задача не завершена")
    except Exception as e:
        print(f"❌ Ошибка: {str(e)[:100]}")
    
    # Итоги
    print("\n══════════════════════════════════════════")
    print(f"ИТОГО: {tests_passed}/{total_tests} тестов пройдено")
    percentage = (tests_passed / total_tests) * 100
    print(f"Успех: {percentage:.0f}%")
    
    if tests_passed >= 4:
        print("\n✅ КАЧЕСТВО СОХРАНЕНО - оптимизация успешна!")
    else:
        print("\n⚠️ Требуется доработка")
    
    print("══════════════════════════════════════════\n")
    
    session.close()

if __name__ == "__main__":
    asyncio.run(verify())
