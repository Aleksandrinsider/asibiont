"""
Тест проактивности агента - стиль бизнес-клуба
Проверяет автоматические предложения мероприятий и контактов
"""

import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.autonomous_agent import HybridAutonomousAgent

async def test_proactive_greeting():
    """Тест утреннего приветствия с проактивными предложениями"""
    print("\n=== ТЕСТ 1: Утреннее приветствие ===")
    agent = HybridAutonomousAgent()
    
    # Имитируем user_id реального пользователя (замените на существующий)
    test_user_id = 123456789  # TODO: замените на реальный user_id с профилем
    
    result = await agent.execute(
        user_message="Привет, доброе утро!",
        user_id=test_user_id
    )
    
    print(f"Ответ агента:\n{result.get('response', 'Нет ответа')}\n")
    
    # Проверяем наличие проактивных элементов
    response = result.get('response', '')
    has_contacts = '@' in response
    has_time = any(word in response for word in ['сегодня', 'завтра', 'вечер', 'утро'])
    has_activity = any(word in response for word in ['встреч', 'пробежк', 'созвон', 'проект'])
    
    print(f"✓ Упоминает контакты: {has_contacts}")
    print(f"✓ Предлагает конкретное время: {has_time}")
    print(f"✓ Предлагает активность: {has_activity}")
    
    return has_contacts or has_time or has_activity

async def test_proactive_task_creation():
    """Тест создания задачи с автоматическим поиском компании"""
    print("\n=== ТЕСТ 2: Создание задачи про активность ===")
    agent = HybridAutonomousAgent()
    
    test_user_id = 123456789  # TODO: замените на реальный
    
    result = await agent.execute(
        user_message="Создай задачу 'пробежка завтра в 19:00'",
        user_id=test_user_id
    )
    
    print(f"Ответ агента:\n{result.get('response', 'Нет ответа')}\n")
    
    # Проверяем автоматический вызов find_relevant_contacts
    response = result.get('response', '')
    has_contact_suggestions = '@' in response or 'нашел' in response.lower()
    task_created = 'создал' in response.lower() or 'задач' in response.lower()
    
    print(f"✓ Задача создана: {task_created}")
    print(f"✓ Предложены контакты: {has_contact_suggestions}")
    
    return task_created and has_contact_suggestions

async def test_proactive_goal_discussion():
    """Тест обсуждения целей с автоматическим поиском партнеров"""
    print("\n=== ТЕСТ 3: Обсуждение целей ===")
    agent = HybridAutonomousAgent()
    
    test_user_id = 123456789  # TODO: замените на реальный
    
    result = await agent.execute(
        user_message="Хочу развивать стартап в AI",
        user_id=test_user_id
    )
    
    print(f"Ответ агента:\n{result.get('response', 'Нет ответа')}\n")
    
    # Проверяем автоматический поиск партнеров
    response = result.get('response', '')
    has_partners = '@' in response or 'нашел' in response.lower() or 'партнер' in response.lower()
    has_action = any(word in response.lower() for word in ['созвон', 'встреч', 'познаком', 'делегир'])
    
    print(f"✓ Предложены партнеры: {has_partners}")
    print(f"✓ Предложены конкретные действия: {has_action}")
    
    return has_partners or has_action

async def test_proactive_context_generation():
    """Тест генерации проактивного контекста"""
    print("\n=== ТЕСТ 4: Генерация проактивного контекста ===")
    agent = HybridAutonomousAgent()
    
    test_user_id = 123456789  # TODO: замените на реальный
    
    # Тестируем метод напрямую
    from models import Session
    from datetime import datetime
    import pytz
    
    session = Session()
    try:
        user_now = datetime.now(pytz.timezone('Europe/Moscow'))
        context = await agent._generate_proactive_context(test_user_id, session, user_now)
        
        print(f"Проактивный контекст:\n{context}\n")
        
        has_time_analysis = 'утро' in context or 'день' in context or 'вечер' in context
        has_interests = 'интерес' in context.lower()
        has_contacts = '@' in context
        
        print(f"✓ Анализ времени суток: {has_time_analysis}")
        print(f"✓ Учитывает интересы: {has_interests}")
        print(f"✓ Включает контакты: {has_contacts}")
        
        return len(context) > 0
    finally:
        session.close()

async def run_all_tests():
    """Запуск всех тестов"""
    print("=" * 60)
    print("ТЕСТИРОВАНИЕ ПРОАКТИВНОСТИ АГЕНТА")
    print("Стиль: Бизнес-клуб")
    print("=" * 60)
    
    results = []
    
    try:
        results.append(("Утреннее приветствие", await test_proactive_greeting()))
    except Exception as e:
        print(f"❌ Ошибка в тесте 1: {e}")
        results.append(("Утреннее приветствие", False))
    
    try:
        results.append(("Создание задачи", await test_proactive_task_creation()))
    except Exception as e:
        print(f"❌ Ошибка в тесте 2: {e}")
        results.append(("Создание задачи", False))
    
    try:
        results.append(("Обсуждение целей", await test_proactive_goal_discussion()))
    except Exception as e:
        print(f"❌ Ошибка в тесте 3: {e}")
        results.append(("Обсуждение целей", False))
    
    try:
        results.append(("Генерация контекста", await test_proactive_context_generation()))
    except Exception as e:
        print(f"❌ Ошибка в тесте 4: {e}")
        results.append(("Генерация контекста", False))
    
    print("\n" + "=" * 60)
    print("РЕЗУЛЬТАТЫ ТЕСТОВ")
    print("=" * 60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"{status}: {test_name}")
    
    print(f"\nИтого: {passed}/{total} тестов прошли успешно")
    print("=" * 60)
    
    if passed == total:
        print("\n🎉 Все тесты пройдены! Агент работает проактивно.")
    else:
        print(f"\n⚠️  Некоторые тесты не прошли ({total - passed}/{total})")
        print("Проверьте:")
        print("1. Наличие пользователя с профилем (интересы, цели)")
        print("2. Наличие других пользователей в базе для поиска партнеров")
        print("3. Правильность user_id в тестах")

if __name__ == "__main__":
    print("\n⚠️  ВАЖНО: Замените test_user_id = 123456789 на реальный ID пользователя")
    print("   с заполненным профилем (интересы, цели) для корректной работы тестов\n")
    
    asyncio.run(run_all_tests())
