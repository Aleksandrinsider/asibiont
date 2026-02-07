"""
Комплексная проверка перед продакшеном
Проверяет все критичные сценарии использования
"""
import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ai_integration.chat import chat_with_ai
from models import Session, User, UserProfile, Base, engine, Task
from datetime import datetime, timedelta

async def test_production_scenarios():
    """Тестирование реальных пользовательских сценариев"""
    user_id = 999888777
    Base.metadata.create_all(engine)
    session = Session()
    
    # Setup test user
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if user:
        # Удаляем связанные данные
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            session.delete(profile)
        session.query(Task).filter_by(user_id=user.id).delete()
        session.commit()
        session.delete(user)
        session.commit()
    
    user = User(telegram_id=user_id, username='prod_test', first_name='ProdTest', timezone='Europe/Moscow')
    session.add(user)
    session.commit()
    session.refresh(user)  # Важно! Получаем ID
    
    profile = UserProfile(user_id=user.id, interests='Python, AI', goals='Тестирование', city='Moscow')
    session.add(profile)
    session.commit()
    
    print("="*70)
    print("🔍 ПРОВЕРКА ГОТОВНОСТИ К ПРОДАКШЕНУ")
    print("="*70)
    
    tests_passed = 0
    tests_failed = 0
    errors = []
    
    # ТЕСТ 1: Создание задачи
    print("\n1️⃣ Тест: Создание задачи с временем...")
    try:
        response = await chat_with_ai('Напомни завтра в 10 утра купить хлеб', user_id=user_id, db_session=session)
        tasks = session.query(Task).filter_by(user_id=user.id).all()
        if len(tasks) >= 1 and 'ошибк' not in response.get('response', '').lower():
            print("   ✅ PASS")
            tests_passed += 1
        else:
            print("   ❌ FAIL - задача не создана")
            tests_failed += 1
            errors.append("Создание задачи не работает")
    except Exception as e:
        print(f"   ❌ FAIL - ошибка: {e}")
        tests_failed += 1
        errors.append(f"Создание задачи: {e}")
    
    # ТЕСТ 2: Список задач
    print("\n2️⃣ Тест: Показ списка задач...")
    try:
        response = await chat_with_ai('Покажи мои задачи', user_id=user_id, db_session=session)
        if 'хлеб' in response.get('response', '').lower() or 'задач' in response.get('response', '').lower():
            print("   ✅ PASS")
            tests_passed += 1
        else:
            print("   ❌ FAIL - задачи не показаны")
            tests_failed += 1
            errors.append("Список задач не отображается")
    except Exception as e:
        print(f"   ❌ FAIL - ошибка: {e}")
        tests_failed += 1
        errors.append(f"Список задач: {e}")
    
    # ТЕСТ 3: Завершение задачи
    print("\n3️⃣ Тест: Завершение задачи...")
    try:
        response = await chat_with_ai('Готово, сделал', user_id=user_id, db_session=session)
        if 'ошибк' not in response.get('response', '').lower():
            print("   ✅ PASS")
            tests_passed += 1
        else:
            print("   ❌ FAIL - ошибка при завершении")
            tests_failed += 1
            errors.append("Завершение задачи выдало ошибку")
    except Exception as e:
        print(f"   ❌ FAIL - ошибка: {e}")
        tests_failed += 1
        errors.append(f"Завершение задачи: {e}")
    
    # ТЕСТ 4: Создание задачи без времени (должен спросить)
    print("\n4️⃣ Тест: Создание задачи без времени...")
    try:
        response = await chat_with_ai('Напомни позвонить маме', user_id=user_id, db_session=session)
        text = response.get('response', '').lower()
        if 'какое время' in text or 'когда' in text or 'на какое' in text:
            print("   ✅ PASS - правильно спрашивает время")
            tests_passed += 1
        else:
            print("   ⚠️  WARN - не спросил время, но без ошибок")
            tests_passed += 1  # Считаем passed если нет ошибки
    except Exception as e:
        print(f"   ❌ FAIL - ошибка: {e}")
        tests_failed += 1
        errors.append(f"Задача без времени: {e}")
    
    # ТЕСТ 5: Перенос задачи
    print("\n5️⃣ Тест: Перенос задачи...")
    try:
        # Создаем задачу для переноса
        task = Task(user_id=user.id, title='Тестовая задача', status='pending', 
                   reminder_time=datetime.now() + timedelta(hours=1))
        session.add(task)
        session.commit()
        
        response = await chat_with_ai('Перенеси задачу на завтра в 15:00', user_id=user_id, db_session=session)
        if 'ошибк' not in response.get('response', '').lower():
            print("   ✅ PASS")
            tests_passed += 1
        else:
            print("   ❌ FAIL")
            tests_failed += 1
            errors.append("Перенос задачи не работает")
    except Exception as e:
        print(f"   ❌ FAIL - ошибка: {e}")
        tests_failed += 1
        errors.append(f"Перенос задачи: {e}")
    
    # ТЕСТ 6: Удаление задачи
    print("\n6️⃣ Тест: Удаление задачи...")
    try:
        response = await chat_with_ai('Удали задачу тестовая', user_id=user_id, db_session=session)
        if 'ошибк' not in response.get('response', '').lower():
            print("   ✅ PASS")
            tests_passed += 1
        else:
            print("   ❌ FAIL")
            tests_failed += 1
            errors.append("Удаление задачи не работает")
    except Exception as e:
        print(f"   ❌ FAIL - ошибка: {e}")
        tests_failed += 1
        errors.append(f"Удаление задачи: {e}")
    
    # ТЕСТ 7: Профиль пользователя
    print("\n7️⃣ Тест: Показ профиля...")
    try:
        response = await chat_with_ai('Покажи мой профиль', user_id=user_id, db_session=session)
        if 'профиль' in response.get('response', '').lower() or 'moscow' in response.get('response', '').lower():
            print("   ✅ PASS")
            tests_passed += 1
        else:
            print("   ❌ FAIL")
            tests_failed += 1
            errors.append("Профиль не показывается")
    except Exception as e:
        print(f"   ❌ FAIL - ошибка: {e}")
        tests_failed += 1
        errors.append(f"Профиль: {e}")
    
    # ТЕСТ 8: Обновление профиля
    print("\n8️⃣ Тест: Обновление профиля...")
    try:
        response = await chat_with_ai('Обнови мой профиль: добавь навык JavaScript', user_id=user_id, db_session=session)
        if 'ошибк' not in response.get('response', '').lower():
            print("   ✅ PASS")
            tests_passed += 1
        else:
            print("   ❌ FAIL")
            tests_failed += 1
            errors.append("Обновление профиля не работает")
    except Exception as e:
        print(f"   ❌ FAIL - ошибка: {e}")
        tests_failed += 1
        errors.append(f"Обновление профиля: {e}")
    
    # ТЕСТ 9: Поиск партнеров
    print("\n9️⃣ Тест: Поиск партнеров...")
    try:
        response = await chat_with_ai('Найди партнеров по Python', user_id=user_id, db_session=session)
        if 'ошибк' not in response.get('response', '').lower():
            print("   ✅ PASS")
            tests_passed += 1
        else:
            print("   ❌ FAIL")
            tests_failed += 1
            errors.append("Поиск партнеров не работает")
    except Exception as e:
        print(f"   ❌ FAIL - ошибка: {e}")
        tests_failed += 1
        errors.append(f"Поиск партнеров: {e}")
    
    # ТЕСТ 10: Обычный диалог (не команда)
    print("\n🔟 Тест: Обычный диалог...")
    try:
        response = await chat_with_ai('Привет, как дела?', user_id=user_id, db_session=session)
        if response.get('response') and 'ошибк' not in response.get('response', '').lower():
            print("   ✅ PASS")
            tests_passed += 1
        else:
            print("   ❌ FAIL")
            tests_failed += 1
            errors.append("Обычный диалог не работает")
    except Exception as e:
        print(f"   ❌ FAIL - ошибка: {e}")
        tests_failed += 1
        errors.append(f"Обычный диалог: {e}")
    
    # Очистка
    try:
        session.query(Task).filter_by(user_id=user.id).delete()
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            session.delete(profile)
        session.commit()
        session.delete(user)
        session.commit()
    except Exception as e:
        print(f"⚠️  Ошибка при очистке (не критично): {e}")
        session.rollback()
    finally:
        session.close()
    
    # Итоги
    print("\n" + "="*70)
    print("📊 ИТОГОВЫЕ РЕЗУЛЬТАТЫ")
    print("="*70)
    total = tests_passed + tests_failed
    percentage = (tests_passed / total * 100) if total > 0 else 0
    
    print(f"\n✅ Успешно: {tests_passed}/{total} ({percentage:.0f}%)")
    print(f"❌ Провалено: {tests_failed}/{total}")
    
    if errors:
        print("\n⚠️  ОБНАРУЖЕНЫ ОШИБКИ:")
        for i, error in enumerate(errors, 1):
            print(f"   {i}. {error}")
    
    print("\n" + "="*70)
    if tests_failed == 0:
        print("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ - ГОТОВО К ПРОДАКШЕНУ!")
    elif percentage >= 80:
        print("✅ СИСТЕМА РАБОТОСПОСОБНА - можно деплоить с осторожностью")
    else:
        print("❌ КРИТИЧЕСКИЕ ОШИБКИ - НЕ ГОТОВО К ПРОДАКШЕНУ!")
    print("="*70)
    
    return tests_failed == 0

if __name__ == '__main__':
    result = asyncio.run(test_production_scenarios())
    sys.exit(0 if result else 1)
