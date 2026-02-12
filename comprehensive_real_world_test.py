import asyncio
import sys
sys.path.append('.')

from ai_integration.autonomous_agent import chat_with_ai
from models import User, SessionLocal

async def comprehensive_function_test():
    """Полный тест всех функций ASI Biont на реальных запросах"""

    print("🧪 ПОЛНЫЙ ТЕСТ ВСЕХ ФУНКЦИЙ ASI BIONT")
    print("=" * 60)

    # Получаем тестового пользователя
    session = SessionLocal()
    user = session.query(User).filter_by(telegram_id=99999).first()
    if not user:
        print("❌ Test user not found!")
        return
    session.close()

    # Реальные пользовательские запросы для тестирования всех функций
    real_world_scenarios = [
        # 1. Приветствия и базовые взаимодействия
        {
            "query": "Привет! Как дела?",
            "expected_tools": ["list_tasks"],
            "category": "Приветствие",
            "description": "Стандартное приветствие"
        },
        {
            "query": "Доброе утро! Что у меня запланировано на сегодня?",
            "expected_tools": ["list_tasks"],
            "category": "Приветствие",
            "description": "Утреннее приветствие с запросом задач"
        },

        # 2. Исследования и анализ информации
        {
            "query": "Расскажи о последних трендах в искусственном интеллекте 2026 года",
            "expected_tools": ["research_topic"],
            "category": "Исследование",
            "description": "Запрос на исследование AI трендов"
        },
        {
            "query": "Какие новые технологии появились в веб-разработке за последний год?",
            "expected_tools": ["research_topic"],
            "category": "Исследование",
            "description": "Технологии веб-разработки"
        },
        {
            "query": "Проанализируй рынок приложений для здоровья и фитнеса",
            "expected_tools": ["research_and_plan"],
            "category": "Анализ рынка",
            "description": "Комплексный анализ рынка"
        },

        # 3. Поиск партнеров и контактов
        {
            "query": "Где найти единомышленников по Python разработке в Москве?",
            "expected_tools": ["find_partners"],
            "category": "Поиск партнеров",
            "description": "Python разработчики в Москве"
        },
        {
            "query": "Ищу партнеров для стартапа в сфере образования",
            "expected_tools": ["find_partners"],
            "category": "Поиск партнеров",
            "description": "Партнеры для образовательного стартапа"
        },
        {
            "query": "Где найти людей для совместных пробежек по утрам?",
            "expected_tools": ["find_partners", "find_relevant_contacts_for_task"],
            "category": "Социальные связи",
            "description": "Поиск партнеров для спорта"
        },

        # 4. Управление задачами
        {
            "query": "Создай задачу: подготовить презентацию для команды завтра к 15:00",
            "expected_tools": ["add_task"],
            "category": "Управление задачами",
            "description": "Создание задачи с конкретным временем"
        },
        {
            "query": "Напомни мне позвонить маме через 2 часа",
            "expected_tools": ["add_task"],
            "category": "Управление задачами",
            "description": "Создание задачи с относительным временем"
        },
        {
            "query": "Я закончил работу над отчетом по продажам",
            "expected_tools": ["complete_task"],
            "category": "Управление задачами",
            "description": "Отметка выполненной задачи"
        },
        {
            "query": "Перенеси встречу с клиентом на завтра в 11:00",
            "expected_tools": ["reschedule_task"],
            "category": "Управление задачами",
            "description": "Перенос времени задачи"
        },

        # 5. Управление профилем
        {
            "query": "Покажи мой профиль",
            "expected_tools": ["show_profile"],
            "category": "Профиль",
            "description": "Просмотр профиля"
        },
        {
            "query": "Обнови мой профиль: я senior Python разработчик, опыт 7 лет, интересуюсь AI и машинным обучением",
            "expected_tools": ["update_profile"],
            "category": "Профиль",
            "description": "Обновление профиля"
        },
        {
            "query": "Добавь в мой профиль навык React и интерес к мобильной разработке",
            "expected_tools": ["smart_update_profile"],
            "category": "Профиль",
            "description": "Умное обновление профиля"
        },

        # 6. Проверка времени и конфликтов
        {
            "query": "Создай задачу: встретиться с инвесторами послезавтра в 14:00",
            "expected_tools": ["check_time_conflicts", "add_task"],
            "category": "Проверка времени",
            "description": "Создание задачи с предварительной проверкой"
        },

        # 7. Новости и тренды
        {
            "query": "Что нового в мире технологий?",
            "expected_tools": ["get_news_trends"],
            "category": "Новости",
            "description": "Запрос новостей"
        },

        # 8. Делегирование задач
        {
            "query": "Нужно найти специалиста по дизайну логотипа для моего проекта",
            "expected_tools": ["delegate_task"],
            "category": "Делегирование",
            "description": "Делегирование задачи специалисту"
        },

        # 9. Мониторинг контактов
        {
            "query": "Следи за новыми Python разработчиками в системе",
            "expected_tools": ["set_contact_alert"],
            "category": "Мониторинг",
            "description": "Настройка мониторинга контактов"
        },

        # 10. Комплексные запросы
        {
            "query": "Я хочу создать новый проект по AI. Помоги проанализировать рынок, найти партнеров и составить план",
            "expected_tools": ["research_and_plan", "find_partners"],
            "category": "Комплексные",
            "description": "Многофункциональный запрос"
        }
    ]

    results = []
    total_tests = len(real_world_scenarios)

    print(f"📋 Всего тестов: {total_tests}")
    print()

    for i, scenario in enumerate(real_world_scenarios, 1):
        print(f"🧪 Тест {i}/{total_tests}: {scenario['category']} - {scenario['description']}")
        print(f"   💬 '{scenario['query']}'")

        try:
            # Вызываем AI
            result = await chat_with_ai(
                message=scenario['query'],
                user_id=user.telegram_id
            )

            if isinstance(result, dict):
                tools_used = result.get('tools_used', [])
                response = result.get('response', '')
                response_length = len(response)

                print(f"   📝 Ответ: {response_length} символов")
                print(f"   🔧 Вызванные инструменты: {tools_used}")
                print(f"   ✅ Ожидались: {scenario['expected_tools']}")

                # Проверяем результат - хотя бы один из ожидаемых инструментов должен быть вызван
                success = any(expected_tool in tools_used for expected_tool in scenario['expected_tools'])

                if success:
                    print("   ✅ УСПЕХ: Правильные инструменты вызваны")
                    status = "✅"
                else:
                    print("   ❌ ПРОВАЛ: Ожидаемые инструменты не вызваны")
                    status = "❌"

                results.append({
                    "test_number": i,
                    "category": scenario['category'],
                    "description": scenario['description'],
                    "query": scenario['query'],
                    "expected": scenario['expected_tools'],
                    "actual": tools_used,
                    "success": success,
                    "response_length": response_length,
                    "status": status
                })

            else:
                print("   ❌ ОШИБКА: Неправильный формат ответа")
                results.append({
                    "test_number": i,
                    "category": scenario['category'],
                    "description": scenario['description'],
                    "query": scenario['query'],
                    "expected": scenario['expected_tools'],
                    "actual": [],
                    "success": False,
                    "response_length": 0,
                    "status": "❌",
                    "error": "Wrong response format"
                })

        except Exception as e:
            print(f"   ❌ ОШИБКА: {str(e)}")
            results.append({
                "test_number": i,
                "category": scenario['category'],
                "description": scenario['description'],
                "query": scenario['query'],
                "expected": scenario['expected_tools'],
                "actual": [],
                "success": False,
                "response_length": 0,
                "status": "❌",
                "error": str(e)
            })

        print()

    # Итоговый отчет
    print("=" * 80)
    print("📊 ПОДРОБНЫЙ ОТЧЕТ ПО ВСЕМ ТЕСТАМ")
    print("=" * 80)

    successful = sum(1 for r in results if r["success"])
    success_rate = successful / total_tests * 100

    print(f"🎯 ОБЩАЯ СТАТИСТИКА:")
    print(f"   ✅ Успешных тестов: {successful}/{total_tests} ({success_rate:.1f}%)")
    print()

    # Статистика по категориям
    categories = {}
    for result in results:
        cat = result['category']
        if cat not in categories:
            categories[cat] = {'total': 0, 'success': 0}
        categories[cat]['total'] += 1
        if result['success']:
            categories[cat]['success'] += 1

    print("📈 СТАТИСТИКА ПО КАТЕГОРИЯМ:")
    for cat, stats in categories.items():
        cat_success_rate = stats['success'] / stats['total'] * 100
        print(f"   {cat}: {stats['success']}/{stats['total']} ({cat_success_rate:.1f}%)")
    print()

    # Детальные результаты
    print("📋 ДЕТАЛЬНЫЕ РЕЗУЛЬТАТЫ:")
    for result in results:
        print(f"{result['test_number']:2d}. {result['status']} {result['category'][:15]:15} | {result['description'][:30]:30} | Ожид: {result['expected']} | Вызв: {result['actual']}")

    print()
    print("=" * 80)

    # Рекомендации
    if success_rate >= 95:
        print("🎉 ОТЛИЧНО! ASI Biont работает на 95%+ реальных запросов!")
        print("   Система готова к продакшену с минимальными доработками.")
    elif success_rate >= 85:
        print("👍 ХОРОШО! ASI Biont работает на 85%+ реальных запросов.")
        print("   Есть небольшие проблемы, но система функциональна.")
    elif success_rate >= 70:
        print("⚠️ УДОВЛЕТВОРИТЕЛЬНО! ASI Biont работает на 70%+ реальных запросов.")
        print("   Требуется доработка некоторых функций.")
    else:
        print("❌ ТРЕБУЕТСЯ ДОРАБОТКА! ASI Biont работает менее чем на 70% запросов.")
        print("   Необходимы существенные улучшения.")

    # Анализ проблемных областей
    failed_tests = [r for r in results if not r['success']]
    if failed_tests:
        print()
        print("🔍 ПРОБЛЕМНЫЕ ОБЛАСТИ:")
        problem_categories = {}
        for test in failed_tests:
            cat = test['category']
            problem_categories[cat] = problem_categories.get(cat, 0) + 1

        for cat, count in sorted(problem_categories.items(), key=lambda x: x[1], reverse=True):
            print(f"   • {cat}: {count} неудачных тестов")

    return results

if __name__ == '__main__':
    asyncio.run(comprehensive_function_test())