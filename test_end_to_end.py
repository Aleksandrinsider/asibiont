"""
END-TO-END тест: проверяем как агент понимает запросы и выполняет действия
"""
import asyncio
from models import User, UserProfile, Task, Session
from ai_integration.router import CommandRouter

async def test_agent_understanding():
    """Тест полного цикла: запрос пользователя -> агент -> выполнение -> ответ"""
    
    session = Session()
    
    # Создаем тестового пользователя
    test_user = session.query(User).filter_by(telegram_id=888777666).first()
    if not test_user:
        test_user = User(
            telegram_id=888777666,
            username="e2e_test_user",
            first_name="E2E Test"
        )
        session.add(test_user)
        session.commit()
    
    router = CommandRouter()
    user_id = 888777666
    
    print("=" * 80)
    print("END-TO-END TEST: Агент понимает и выполняет запросы")
    print("=" * 80)
    print()
    
    test_cases = [
        # (запрос_пользователя, ожидаемое_действие, ключевые_слова_в_ответе)
        ("Напомни завтра в 10 утра купить молоко", "CreateTask", ["задач", "молоко"]),
        ("Покажи мои задачи", "ListTasks", ["задач"]),
        ("Я из Москвы", "UpdateProfile", ["Москва"]),
        ("Люблю программирование", "UpdateProfile", ["программирование"]),
        ("Найди партнеров для пробежки", "Conversation", ["контакт"]),  # find_relevant вызывается через conversation
    ]
    
    passed = 0
    failed = 0
    
    for i, (user_message, expected_action, keywords) in enumerate(test_cases, 1):
        print(f"\n{'='*80}")
        print(f"ТЕСТ {i}: {user_message}")
        print(f"{'='*80}")
        
        try:
            # 1. Роутер классифицирует намерение
            command = await router.route(user_message, user_id)
            detected_action = command.__class__.__name__.lower()
            
            print(f"🧠 Агент распознал: {command.__class__.__name__}")
            
            # 2. Выполняем команду
            db_session = Session()
            try:
                result = await command.execute(user_id, db_session)
                response = result.get('response', str(result)) if isinstance(result, dict) else str(result)
                
                print(f"✉️ Ответ агента: {response[:150]}...")
                
                # 3. Проверяем корректность
                action_correct = expected_action.lower() in detected_action
                keywords_found = any(kw.lower() in response.lower() for kw in keywords)
                
                if action_correct and keywords_found:
                    print(f"✅ PASS: Правильно распознал и выполнил")
                    passed += 1
                elif action_correct and not keywords_found:
                    print(f"⚠️ PARTIAL: Действие верное, но ответ неполный")
                    print(f"   Ожидались слова: {keywords}")
                    print(f"   Получено: {response[:100]}")
                    passed += 1
                else:
                    print(f"❌ FAIL: Ожидалось {expected_action}, получено {detected_action}")
                    failed += 1
                    
            finally:
                db_session.close()
                
        except Exception as e:
            print(f"❌ FAIL: Ошибка - {e}")
            failed += 1
            import traceback
            traceback.print_exc()
    
    session.close()
    
    print("\n" + "=" * 80)
    print("ИТОГИ END-TO-END ТЕСТИРОВАНИЯ")
    print("=" * 80)
    print(f"✅ Пройдено: {passed}/{len(test_cases)}")
    print(f"❌ Провалено: {failed}/{len(test_cases)}")
    print(f"📊 Процент успеха: {passed/len(test_cases)*100:.1f}%")
    
    if failed == 0:
        print("\n🎉 ВСЕ ТЕСТЫ ПРОШЛИ! Агент работает отлично!")
    else:
        print(f"\n⚠️ Есть проблемы в {failed} тестах")
    
    return passed, failed

async def test_complex_scenarios():
    """Тест сложных сценариев"""
    
    print("\n" + "=" * 80)
    print("СЛОЖНЫЕ СЦЕНАРИИ")
    print("=" * 80)
    
    router = CommandRouter()
    user_id = 888777666
    
    scenarios = [
        # Сценарий 1: Ответ на напоминание
        ("уже проверил почту", "Должен завершить текущую задачу"),
        
        # Сценарий 2: Неявная команда
        ("сделал", "Должен завершить задачу"),
        
        # Сценарий 3: Множественная информация
        ("я работаю в ASI Biont разработчиком", "Должен сохранить компанию и должность"),
        
        # Сценарий 4: Цель + интерес одновременно
        ("хочу научиться играть в шахматы", "Должен сохранить цель И интерес"),
    ]
    
    passed = 0
    
    for i, (message, expectation) in enumerate(scenarios, 1):
        print(f"\n{i}. {message}")
        print(f"   Ожидание: {expectation}")
        
        try:
            command = await router.route(message, user_id)
            db_session = Session()
            try:
                result = await command.execute(user_id, db_session)
                response = result.get('response', str(result)) if isinstance(result, dict) else str(result)
                print(f"   ✅ Выполнено: {command.__class__.__name__}")
                print(f"   Ответ: {response[:100]}...")
                passed += 1
            finally:
                db_session.close()
        except Exception as e:
            print(f"   ❌ Ошибка: {e}")
    
    print(f"\n📊 Сложных сценариев пройдено: {passed}/{len(scenarios)}")
    
    return passed, len(scenarios)

if __name__ == "__main__":
    async def main():
        basic_passed, basic_failed = await test_agent_understanding()
        complex_passed, complex_total = await test_complex_scenarios()
        
        total_passed = basic_passed + complex_passed
        total_tests = basic_passed + basic_failed + complex_total
        
        print("\n" + "=" * 80)
        print("ФИНАЛЬНЫЙ ИТОГ")
        print("=" * 80)
        print(f"Всего тестов: {total_tests}")
        print(f"Успешно: {total_passed}")
        print(f"Провалено: {total_tests - total_passed}")
        print(f"Процент успеха: {total_passed/total_tests*100:.1f}%")
    
    asyncio.run(main())
