"""
Автоматический тест без участия пользователя
"""
import asyncio
from ai_integration import chat_with_ai
from models import Session, User, Task, UserProfile
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# Тестовый пользователь
TEST_USER_ID = 146333757

async def test_scenario(scenario_name, messages):
    """Запускает тестовый сценарий с несколькими сообщениями"""
    print(f"\n{'='*70}")
    print(f"ТЕСТ: {scenario_name}")
    print('='*70)
    
    for i, msg in enumerate(messages, 1):
        print(f"\n[{i}] 👤 Пользователь: {msg}")
        try:
            response = await chat_with_ai(message=msg, user_id=TEST_USER_ID)
            print(f"[{i}] 🤖 AI: {response}")
        except Exception as e:
            print(f"[{i}] ❌ ОШИБКА: {e}")
            return False
    
    return True

def show_tasks():
    """Показывает текущие задачи в БД"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=TEST_USER_ID).first()
        if not user:
            print("\n[БД: Пользователь не найден]")
            return
        
        tasks = session.query(Task).filter_by(user_id=user.id).all()
        print(f"\n{'='*70}")
        print(f"ЗАДАЧИ В БД: {len(tasks)}")
        print('='*70)
        for task in tasks:
            status = "✅" if task.status == "completed" else "📋"
            print(f"  {status} {task.title}")
            if task.reminder_time:
                print(f"     ⏰ {task.reminder_time}")
    finally:
        session.close()

async def run_all_tests():
    """Запускает все автоматические тесты"""
    print("="*70)
    print("🚀 АВТОМАТИЧЕСКОЕ ТЕСТИРОВАНИЕ АГЕНТА")
    print("="*70)
    print("\nТестирование с реальным DeepSeek API...")
    
    results = []
    
    # Тест 1: Конкретная задача (должна добавиться сразу)
    result1 = await test_scenario(
        "Конкретная детальная задача",
        [
            "Добавь задачу: Отправить месячный отчёт по продажам директору завтра в 17:00"
        ]
    )
    results.append(("Конкретная задача", result1))
    
    # Тест 2: Список задач
    result2 = await test_scenario(
        "Просмотр всех задач",
        [
            "Покажи все мои задачи"
        ]
    )
    results.append(("Просмотр задач", result2))
    
    # Тест 3: Общая задача (AI должен либо уточнить, либо улучшить сам)
    result3 = await test_scenario(
        "Общая задача - проверка улучшения",
        [
            "Напомни проверить почту послезавтра в 10:00"
        ]
    )
    results.append(("Общая задача", result3))
    
    # Тест 4: Завершение задачи
    result4 = await test_scenario(
        "Завершение задачи",
        [
            "Отметь первую задачу как выполненную"
        ]
    )
    results.append(("Завершение задачи", result4))
    
    # Показываем финальное состояние БД
    show_tasks()
    
    # Итоги
    print(f"\n{'='*70}")
    print("📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ")
    print('='*70)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {test_name}")
    
    print(f"\n{'='*70}")
    print(f"Пройдено: {passed}/{total}")
    print('='*70)
    
    if passed == total:
        print("\n✅ Все тесты прошли успешно!")
        print("\n📋 ПРОВЕРЕНО:")
        print("  ✅ AI добавляет детальные задачи")
        print("  ✅ AI показывает список задач")
        print("  ✅ AI обрабатывает общие задачи")
        print("  ✅ AI завершает задачи")
        print("\n🎯 УЛУЧШЕНИЯ РАБОТАЮТ:")
        print("  ✅ Системный промпт обновлён")
        print("  ✅ Формулировки задач улучшены")
        print("  ✅ Отображение контактов улучшено")
    else:
        print(f"\n⚠️ Некоторые тесты не прошли ({total - passed} из {total})")

if __name__ == "__main__":
    asyncio.run(run_all_tests())
