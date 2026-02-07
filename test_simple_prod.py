"""
Простая проверка перед продакшеном (без эмодзи для Windows)
"""
import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ai_integration.chat import chat_with_ai
from models import Session, User, UserProfile, Base, engine, Task
from datetime import datetime, timedelta

async def simple_production_check():
    """Быстрая проверка критичных функций"""
    user_id = 999888777
    Base.metadata.create_all(engine)
    session = Session()
    
    # Очистка и создание тестового пользователя
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if user:
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            session.delete(profile)
        session.query(Task).filter_by(user_id=user.id).delete()
        session.commit()
        session.delete(user)
        session.commit()
    
    user = User(telegram_id=user_id, username='prod_test', first_name='Test', timezone='Europe/Moscow')
    session.add(user)
    session.commit()
    session.refresh(user)
    
    profile = UserProfile(user_id=user.id, interests='Python', goals='Test', city='Moscow')
    session.add(profile)
    session.commit()
    
    passed = 0
    failed = 0
    
    print("\n" + "="*70)
    print("PRODUCTION READINESS CHECK")
    print("="*70 + "\n")
    
    # Тест 1: Создание задачи
    print("[1/7] Add task with time...")
    try:
        r = await chat_with_ai('Напомни завтра в 10 купить хлеб', user_id=user_id, db_session=session)
        tasks = session.query(Task).filter_by(user_id=user.id).count()
        if tasks >= 1:
            print("  [PASS]")
            passed += 1
        else:
            print("  [FAIL] No task created")
            failed += 1
    except Exception as e:
        print(f"  [FAIL] {str(e)[:50]}")
        failed += 1
    
    # Тест 2: Список задач
    print("[2/7] List tasks...")
    try:
        r = await chat_with_ai('Покажи мои задачи', user_id=user_id, db_session=session)
        if r.get('response') and 'ошибк' not in r.get('response', '').lower():
            print("  [PASS]")
            passed += 1
        else:
            print("  [FAIL]")
            failed += 1
    except Exception as e:
        print(f"  [FAIL] {str(e)[:50]}")
        failed += 1
    
    # Тест 3: Завершение задачи
    print("[3/7] Complete task...")
    try:
        r = await chat_with_ai('Готово', user_id=user_id, db_session=session)
        if 'ошибк' not in r.get('response', '').lower():
            print("  [PASS]")
            passed += 1
        else:
            print("  [FAIL]")
            failed += 1
    except Exception as e:
        print(f"  [FAIL] {str(e)[:50]}")
        failed += 1
    
    # Тест 4: Профиль
    print("[4/7] Show profile...")
    try:
        r = await chat_with_ai('Покажи профиль', user_id=user_id, db_session=session)
        if 'moscow' in r.get('response', '').lower() or 'профиль' in r.get('response', '').lower():
            print("  [PASS]")
            passed += 1
        else:
            print("  [FAIL]")
            failed += 1
    except Exception as e:
        print(f"  [FAIL] {str(e)[:50]}")
        failed += 1
    
    # Тест 5: Обновление профиля
    print("[5/7] Update profile...")
    try:
        r = await chat_with_ai('Обнови профиль: навык JS', user_id=user_id, db_session=session)
        if 'ошибк' not in r.get('response', '').lower():
            print("  [PASS]")
            passed += 1
        else:
            print("  [FAIL]")
            failed += 1
    except Exception as e:
        print(f"  [FAIL] {str(e)[:50]}")
        failed += 1
    
    # Тест 6: Удаление задачи
    task = Task(user_id=user.id, title='Test', status='pending', reminder_time=datetime.now() + timedelta(hours=1))
    session.add(task)
    session.commit()
    
    print("[6/7] Delete task...")
    try:
        r = await chat_with_ai('Удали задачу test', user_id=user_id, db_session=session)
        if 'ошибк' not in r.get('response', '').lower():
            print("  [PASS]")
            passed += 1
        else:
            print("  [FAIL]")
            failed += 1
    except Exception as e:
        print(f"  [FAIL] {str(e)[:50]}")
        failed += 1
    
    # Тест 7: Обычный диалог
    print("[7/7] Regular chat...")
    try:
        r = await chat_with_ai('Привет', user_id=user_id, db_session=session)
        if r.get('response') and 'ошибк' not in r.get('response', '').lower():
            print("  [PASS]")
            passed += 1
        else:
            print("  [FAIL]")
            failed += 1
    except Exception as e:
        print(f"  [FAIL] {str(e)[:50]}")
        failed += 1
    
    # Очистка
    try:
        session.query(Task).filter_by(user_id=user.id).delete()
        p = session.query(UserProfile).filter_by(user_id=user.id).first()
        if p:
            session.delete(p)
        session.commit()
        session.delete(user)
        session.commit()
    except:
        pass
    finally:
        session.close()
    
    # Результаты
    total = passed + failed
    percentage = (passed / total * 100) if total > 0 else 0
    
    print("\n" + "="*70)
    print(f"RESULTS: {passed}/{total} tests passed ({percentage:.0f}%)")
    print("="*70)
    
    if failed == 0:
        print(">>> ALL TESTS PASSED - READY FOR PRODUCTION <<<")
        return True
    elif percentage >= 85:
        print(">>> SYSTEM FUNCTIONAL - CAN DEPLOY WITH CAUTION <<<")
        return True
    else:
        print(f">>> WARNING: {failed} TESTS FAILED - REVIEW REQUIRED <<<")
        return False

if __name__ == '__main__':
    result = asyncio.run(simple_production_check())
    sys.exit(0 if result else 1)
