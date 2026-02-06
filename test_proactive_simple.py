"""
Упрощенный тест проактивности агента
Тестирует только генерацию проактивного контекста без эмодзи
"""

import asyncio
import sys
import os

# Настройка кодировки для Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.autonomous_agent import HybridAutonomousAgent
from models import Session, User, UserProfile, Task
from datetime import datetime, timedelta
import pytz

def setup_test_data():
    """Создает тестовые данные если их нет"""
    session = Session()
    try:
        # Создаем тестового пользователя
        user = session.query(User).filter_by(telegram_id=999999999).first()
        if not user:
            user = User(
                telegram_id=999999999,
                username="test_user_proactive",
                timezone="Europe/Moscow"
            )
            session.add(user)
            session.commit()
            print(f"Создан тестовый пользователь: {user.username}")
        
        # Создаем профиль
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            profile = UserProfile(
                user_id=user.id,
                city="Москва",
                interests="спорт, бег, программирование, AI, стартапы",
                skills="Python, JavaScript, маркетинг",
                goals="развивать стартап, улучшить здоровье, изучить AI"
            )
            session.add(profile)
        else:
            profile.city = "Москва"
            profile.interests = "спорт, бег, программирование, AI, стартапы"
            profile.skills = "Python, JavaScript, маркетинг"
            profile.goals = "развивать стартап, улучшить здоровье, изучить AI"
        
        session.commit()
        
        # Создаем несколько задач
        tasks = session.query(Task).filter_by(user_id=user.id).all()
        if not tasks or len(tasks) < 2:
            # Чистим старые
            for t in tasks:
                session.delete(t)
            
            # Создаем новые
            now = datetime.now(pytz.UTC)
            
            # Просроченная задача
            task1 = Task(
                user_id=user.id,
                title="Подготовить презентацию",
                reminder_time=now - timedelta(days=2),
                status="pending"
            )
            
            # Сегодняшняя задача
            task2 = Task(
                user_id=user.id,
                title="Пробежка в парке",
                reminder_time=now + timedelta(hours=9),
                status="pending"
            )
            
            session.add(task1)
            session.add(task2)
            session.commit()
            print("Созданы тестовые задачи")
        
        return user.telegram_id
        
    except Exception as e:
        print(f"Ошибка создания тестовых данных: {e}")
        session.rollback()
        return None
    finally:
        session.close()

async def test_proactive_context():
    """Тест генерации проактивного контекста"""
    print("\n" + "="*70)
    print("ТЕСТ ГЕНЕРАЦИИ ПРОАКТИВНОГО КОНТЕКСТА")
    print("="*70)
    
    user_id = setup_test_data()
    if not user_id:
        print("[FAIL] Не удалось создать тестовые данные")
        return False
    
    agent = HybridAutonomousAgent()
    session = Session()
    
    try:
        user_now = datetime.now(pytz.timezone('Europe/Moscow'))
        print(f"\nТекущее время: {user_now.strftime('%H:%M, %d.%m.%Y')}")
        
        context = await agent._generate_proactive_context(user_id, session, user_now)
        
        print(f"\n--- ПРОАКТИВНЫЙ КОНТЕКСТ ---")
        print(context if context else "(пустой)")
        print("----------------------------\n")
        
        # Проверки
        checks = {
            "Не пустой": len(context) > 0,
            "Анализ времени": any(word in context.lower() for word in ['утро', 'день', 'вечер', 'ночь']),
            "Упоминает интересы": 'интерес' in context.lower(),
            "Упоминает цели": 'цел' in context.lower(),
            "Анализ задач": any(word in context.lower() for word in ['задач', 'запланирован', 'просрочен'])
        }
        
        print("РЕЗУЛЬТАТЫ ПРОВЕРОК:")
        passed = 0
        for check_name, result in checks.items():
            status = "[PASS]" if result else "[FAIL]"
            print(f"  {status} {check_name}")
            if result:
                passed += 1
        
        print(f"\nИтого: {passed}/{len(checks)} проверок прошли")
        return passed >= 3  # Минимум 3 из 5
        
    except Exception as e:
        print(f"[FAIL] Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        session.close()

async def test_planning_with_proactive():
    """Тест планирования с проактивным контекстом"""
    print("\n" + "="*70)
    print("ТЕСТ ПЛАНИРОВАНИЯ С ПРОАКТИВНЫМ КОНТЕКСТОМ")
    print("="*70)
    
    user_id = setup_test_data()
    if not user_id:
        print("[FAIL] Не удалось создать тестовые данные")
        return False
    
    agent = HybridAutonomousAgent()
    
    try:
        # Тест 1: Простое приветствие
        print("\n--- ТЕСТ: Утреннее приветствие ---")
        result = await agent.plan_strategy(
            user_message="Привет, доброе утро!",
            user_id=user_id
        )
        
        print(f"Результат планирования:")
        print(f"  Intent: {result.get('intent')}")
        print(f"  Actions: {len(result.get('actions', []))}")
        print(f"  Strategy: {result.get('response_strategy')}")
        
        # Тест 2: Создание задачи про активность
        print("\n--- ТЕСТ: Создание задачи про пробежку ---")
        result2 = await agent.plan_strategy(
            user_message="Создай задачу 'пробежка завтра в 19:00'",
            user_id=user_id
        )
        
        print(f"Результат планирования:")
        print(f"  Intent: {result2.get('intent')}")
        print(f"  Actions: {result2.get('actions', [])}")
        
        # Проверим, вызывается ли find_relevant_contacts автоматически
        actions = result2.get('actions', [])
        has_add_task = any(a.get('tool') == 'add_task' for a in actions)
        has_find_contacts = any(a.get('tool') == 'find_relevant_contacts_for_task' for a in actions)
        
        print(f"\n  [{'PASS' if has_add_task else 'FAIL'}] add_task вызван")
        print(f"  [{'PASS' if has_find_contacts else 'FAIL'}] find_relevant_contacts_for_task вызван автоматически")
        
        return has_add_task
        
    except Exception as e:
        print(f"[FAIL] Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_full_execution():
    """Тест полного выполнения с ответом"""
    print("\n" + "="*70)
    print("ТЕСТ ПОЛНОГО ВЫПОЛНЕНИЯ (с ответом AI)")
    print("="*70)
    
    user_id = setup_test_data()
    if not user_id:
        print("[FAIL] Не удалось создать тестовые данные")
        return False
    
    agent = HybridAutonomousAgent()
    
    try:
        print("\n--- ЗАПРОС: 'Привет!' ---\n")
        response = await agent.process_request(
            user_message="Привет!",
            user_id=user_id
        )
        
        # process_request возвращает строку напрямую
        if isinstance(response, dict):
            response = response.get('response', '')
        
        print(f"ОТВЕТ АГЕНТА:\n{response}\n")
        
        # Проверки проактивности
        checks = {
            "Ответ не пустой": len(response) > 0,
            "Упоминает контакты": '@' in response,
            "Предлагает время": any(word in response for word in ['сегодня', 'завтра', 'вечер', 'утро', '19:00']),
            "Предлагает активность": any(word in response.lower() for word in ['встреч', 'пробежк', 'созвон', 'проект', 'задач']),
            "Упоминает цели/интересы": any(word in response.lower() for word in ['стартап', 'ai', 'спорт', 'бег'])
        }
        
        print("ПРОВЕРКА ПРОАКТИВНОСТИ:")
        passed = 0
        for check_name, result_check in checks.items():
            status = "[PASS]" if result_check else "[INFO]"
            print(f"  {status} {check_name}")
            if result_check:
                passed += 1
        
        print(f"\nПроактивность: {passed}/{len(checks)} элементов")
        print("(минимум 2 элемента для успеха)")
        
        return passed >= 2
        
    except Exception as e:
        print(f"[FAIL] Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    """Запуск всех тестов"""
    print("\n" + "="*70)
    print("ТЕСТИРОВАНИЕ ПРОАКТИВНОСТИ АГЕНТА")
    print("="*70)
    
    results = []
    
    # Тест 1: Генерация контекста
    try:
        result1 = await test_proactive_context()
        results.append(("Генерация проактивного контекста", result1))
    except Exception as e:
        print(f"\n[ERROR] Тест 1 упал: {e}")
        results.append(("Генерация проактивного контекста", False))
    
    # Тест 2: Планирование
    try:
        result2 = await test_planning_with_proactive()
        results.append(("Планирование с проактивностью", result2))
    except Exception as e:
        print(f"\n[ERROR] Тест 2 упал: {e}")
        results.append(("Планирование с проактивностью", False))
    
    # Тест 3: Полное выполнение
    try:
        result3 = await test_full_execution()
        results.append(("Полное выполнение с AI", result3))
    except Exception as e:
        print(f"\n[ERROR] Тест 3 упал: {e}")
        results.append(("Полное выполнение с AI", False))
    
    # Итоги
    print("\n" + "="*70)
    print("ИТОГОВЫЕ РЕЗУЛЬТАТЫ")
    print("="*70)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "[PASS]" if result else "[FAIL]"
        print(f"{status} {test_name}")
    
    print(f"\nИтого: {passed}/{total} тестов прошли")
    print("="*70)
    
    if passed == total:
        print("\n[SUCCESS] Все тесты пройдены! Проактивность работает.")
        return 0
    elif passed > 0:
        print(f"\n[PARTIAL] Некоторые тесты пройдены ({passed}/{total})")
        print("Проактивность частично работает, но есть проблемы.")
        return 1
    else:
        print("\n[FAILED] Все тесты провалились.")
        print("Проверьте настройки и подключение к API.")
        return 2

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
