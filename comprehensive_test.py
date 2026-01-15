import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import Session, Task, UserProfile
from improved_prompts_final import ai_classify_intent
from config import DEEPSEEK_API_KEY

async def comprehensive_agent_test():
    """Комплексное тестирование всех функций агента"""

    print("🚀 КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ АГЕНТА ASI BIONT")
    print("=" * 60)

    user_id = 12345  # Тестовый пользователь

    # Очистка предыдущих данных
    session = Session()
    try:
        # Удаляем тестовые задачи
        session.query(Task).filter(Task.user_id == 1).delete()
        # Удаляем тестовый профиль
        session.query(UserProfile).filter(UserProfile.user_id == 1).delete()
        session.commit()
        print("✅ Очищены предыдущие тестовые данные")
    except Exception as e:
        print(f"⚠️ Ошибка очистки данных: {e}")
    finally:
        session.close()

    test_cases = [
        # 1. Создание задач
        ("Напомни мне позвонить маме завтра в 10 утра", "add_task", "Создание задачи с временем"),
        ("Добавь задачу: купить продукты в магазине", "add_task", "Создание задачи без времени"),
        ("Запомни: подготовить отчет к пятнице", "add_task", "Создание задачи с относительным временем"),

        # 2. Просмотр задач
        ("Покажи мои задачи", "list_tasks", "Просмотр списка задач"),
        ("Что у меня запланировано?", "list_tasks", "Просмотр задач альтернативной формулировкой"),

        # 3. Выполнение задач
        ("Я позвонил маме", "complete_task", "Отметка задачи как выполненной"),
        ("Готово с покупкой продуктов", "complete_task", "Выполнение задачи"),

        # 4. Обновление профиля
        ("Мой город Москва, я разработчик", "update_profile", "Обновление профиля"),
        ("Я работаю в IT компании", "update_profile", "Дополнение профиля"),

        # 5. Обычный чат
        ("Расскажи анекдот", "chat", "Обычное общение"),
        ("Как дела?", "chat", "Приветствие"),

        # 6. Делегирование задач
        ("Поручи @testuser проверить код", "delegate_task", "Делегирование задачи"),
    ]

    results = []

    for i, (message, expected_intent, description) in enumerate(test_cases, 1):
        print(f"\n🧪 ТЕСТ {i}: {description}")
        print(f"📝 Сообщение: '{message}'")
        print(f"🎯 Ожидаемое намерение: {expected_intent}")

        try:
            # 1. Проверяем AI-классификацию
            intent = await ai_classify_intent(message, api_key=DEEPSEEK_API_KEY)
            intent_correct = intent['type'] == expected_intent
            print(f"🤖 AI-классификация: {intent['type']} (уверенность: {intent['confidence']:.2f}) {'✅' if intent_correct else '❌'}")

            # 2. Тестируем полный чат
            response = await chat_with_ai(message, context=[], user_id=user_id)
            response_length = len(response)
            print(f"💬 Ответ агента: {response[:100]}{'...' if response_length > 100 else ''}")
            print(f"📏 Длина ответа: {response_length} символов")

            # 3. Проверяем сохранение данных в БД
            session = Session()
            try:
                tasks_count = session.query(Task).filter_by(user_id=1).count()
                profile = session.query(UserProfile).filter_by(user_id=1).first()
                profile_filled = bool(profile and (profile.city or profile.company or profile.position))
                print(f"💾 БД статус: задач - {tasks_count}, профиль заполнен - {'✅' if profile_filled else '❌'}")
            finally:
                session.close()

            # Оценка результата
            success = intent_correct and response_length > 10
            results.append({
                'test': description,
                'intent_correct': intent_correct,
                'response_length': response_length,
                'success': success
            })

            print(f"🎉 Результат: {'✅ ПРОЙДЕН' if success else '❌ ПРОВАЛЕН'}")

        except Exception as e:
            print(f"💥 ОШИБКА: {e}")
            results.append({
                'test': description,
                'intent_correct': False,
                'response_length': 0,
                'success': False,
                'error': str(e)
            })

    # Финальная статистика
    print("\n" + "=" * 60)
    print("📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ")

    total_tests = len(results)
    passed_tests = sum(1 for r in results if r['success'])
    success_rate = (passed_tests / total_tests) * 100

    print(f"Всего тестов: {total_tests}")
    print(f"Пройдено: {passed_tests}")
    print(f"Успешность: {success_rate:.1f}%")

    # Детализация по категориям
    categories = {}
    for result in results:
        category = result['test'].split(':')[0] if ':' in result['test'] else 'Разное'
        if category not in categories:
            categories[category] = []
        categories[category].append(result['success'])

    print("\n📈 ПО КАТЕГОРИЯМ:")
    for category, successes in categories.items():
        cat_passed = sum(successes)
        cat_total = len(successes)
        cat_rate = (cat_passed / cat_total) * 100
        print(f"  {category}: {cat_passed}/{cat_total} ({cat_rate:.1f}%)")

    # Финальный вердикт
    if success_rate >= 90:
        print("\n🎉 ОТЛИЧНЫЙ РЕЗУЛЬТАТ! Агент работает стабильно.")
    elif success_rate >= 75:
        print("\n👍 ХОРОШИЙ РЕЗУЛЬТАТ! Агент функционирует нормально.")
    elif success_rate >= 50:
        print("\n⚠️ УДОВЛЕТВОРИТЕЛЬНЫЙ РЕЗУЛЬТАТ! Есть проблемы для исправления.")
    else:
        print("\n💥 КРИТИЧЕСКИЕ ПРОБЛЕМЫ! Требуется доработка.")

    return results

if __name__ == "__main__":
    asyncio.run(comprehensive_agent_test())