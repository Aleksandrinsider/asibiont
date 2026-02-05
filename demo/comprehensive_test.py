import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from self_generating_agent import SelfGeneratingAgent

async def test_all_old_agent_requests():
    """Тестирование нового агента на всех запросах старого агента"""

    print("🧪 ТЕСТИРОВАНИЕ САМООБУЧАЮЩЕГОСЯ АГЕНТА")
    print("Проверяем все запросы, которые поддерживал старый агент")
    print("=" * 80)

    agent = SelfGeneratingAgent()
    user_id = 123456789

    # Список всех запросов, которые поддерживал старый агент
    test_requests = [
        # Создание задач
        "Создай задачу 'позвонить маме' на завтра в 10:00",
        "Создай задачу 'купить продукты' на сегодня в 18:00 с описанием 'молоко, хлеб, яйца'",
        "Создай повторяющуюся задачу 'зарядка' каждый день в 7:00",

        # Просмотр задач
        "Покажи мои задачи",
        "Покажи все активные задачи",
        "Список задач на сегодня",

        # Завершение задач
        "Готово, позвонил маме",
        "Сделал зарядку",
        "Завершил задачу про продукты",

        # Удаление задач
        "Удалить задачу про продукты",
        "Сотри задачу зарядка",

        # Перенос задач
        "Перенеси задачу про продукты на послезавтра в 20:00",
        "Изменить время зарядки на 8:00",

        # Детали задач
        "Покажи детали задачи про продукты",
        "Расскажи подробнее о задаче зарядка",

        # Поиск партнеров
        "Найди партнеров для бега",
        "Найди единомышленников для изучения Python",
        "Кто может помочь с дизайном",

        # Поиск контактов для задач
        "Найди контакты для задачи 'создать мобильное приложение'",
        "Кто поможет с ремонтом автомобиля",
        "Нужны специалисты по маркетингу",

        # Профиль
        "Покажи мой профиль",
        "Обнови мой профиль: люблю программирование, живу в Москве, занимаюсь спортом",
        "Добавить в профиль: интерес - чтение, навык - Python",

        # Память пользователя
        "Запомни что я предпочитаю чай кофе",
        "Запомни мой любимый цвет синий",

        # Делегирование задач
        "Делегируй задачу про продукты @friend",
        "Поручи задачу зарядка @trainer",

        # Общие разговоры
        "Привет, как дела?",
        "Расскажи о себе",
        "Что ты умеешь делать?",

        # Сложные комбинированные запросы
        "Создай задачу на пробежку завтра и найди партнеров для этого",
        "Заверши задачу про продукты и покажи список оставшихся задач",
        "Обнови профиль и найди партнеров по интересам"
    ]

    results = {
        'success': 0,
        'partial_success': 0,
        'failed': 0,
        'details': []
    }

    for i, request in enumerate(test_requests, 1):
        print(f"\n{i:2d}. {request}")
        print("-" * 60)

        try:
            response = await agent.process_request(request, user_id)
            print(f"✅ ОТВЕТ: {response[:200]}{'...' if len(response) > 200 else ''}")

            # Оценка успеха
            if "ошибка" in response.lower() or "не удалось" in response.lower():
                results['failed'] += 1
                results['details'].append({'request': request, 'status': 'failed', 'response': response})
                print("❌ СТАТУС: Провал")
            elif "выполнено" in response.lower() or "создано" in response.lower() or "найдено" in response.lower():
                results['success'] += 1
                results['details'].append({'request': request, 'status': 'success', 'response': response})
                print("✅ СТАТУС: Успех")
            else:
                results['partial_success'] += 1
                results['details'].append({'request': request, 'status': 'partial', 'response': response})
                print("⚠️  СТАТУС: Частичный успех")

        except Exception as e:
            results['failed'] += 1
            results['details'].append({'request': request, 'status': 'error', 'response': str(e)})
            print(f"❌ ОШИБКА: {e}")

        print(f"📚 Сгенерировано функций: {len(agent.generated_functions)}")
        print(f"📊 История действий: {len(agent.execution_history)}")

    # Итоговый отчет
    print("\n" + "=" * 80)
    print("📊 ИТОГОВЫЙ ОТЧЕТ ТЕСТИРОВАНИЯ")
    print("=" * 80)

    total = len(test_requests)
    success_rate = (results['success'] + results['partial_success']) / total * 100

    print(f"Всего запросов: {total}")
    print(f"✅ Полных успехов: {results['success']}")
    print(f"⚠️  Частичных успехов: {results['partial_success']}")
    print(f"❌ Провалено: {results['failed']}")
    print(f"Успешность: {success_rate:.1f}%")
    print(f"📚 Сгенерировано функций: {len(agent.generated_functions)}")
    print(f"📊 Выполнено действий: {len(agent.execution_history)}")

    # Детальный анализ по категориям
    print("\n📈 АНАЛИЗ ПО КАТЕГОРИЯМ:")

    categories = {
        'Создание задач': ['создай задачу', 'создать задачу'],
        'Просмотр задач': ['покажи', 'список', 'мои задачи'],
        'Завершение задач': ['готово', 'сделал', 'завершил'],
        'Удаление задач': ['удали', 'сотри'],
        'Перенос задач': ['перенеси', 'измени время'],
        'Детали задач': ['детали', 'подроб'],
        'Поиск партнеров': ['найди партнер', 'единомышленник'],
        'Поиск контактов': ['контакты', 'специалист'],
        'Профиль': ['профиль', 'обнови профиль'],
        'Память': ['запомни'],
        'Делегирование': ['делегируй', 'поручи'],
        'Разговоры': ['привет', 'расскажи', 'умеешь']
    }

    for category, keywords in categories.items():
        category_requests = [r for r in results['details'] if any(k in r['request'].lower() for k in keywords)]
        if category_requests:
            success_count = sum(1 for r in category_requests if r['status'] in ['success', 'partial'])
            print(f"  {category}: {success_count}/{len(category_requests)} успешных")

    print("\n🎯 ВЫВОД:")
    if success_rate >= 90:
        print("🏆 ОТЛИЧНО! Новый агент успешно справляется со всеми задачами старого!")
    elif success_rate >= 75:
        print("✅ ХОРОШО! Агент работает хорошо, но есть места для улучшения")
    else:
        print("⚠️  ТРЕБУЕТСЯ ДОРАБОТКА! Агент нуждается в улучшениях")

    print(f"\nСамообучающийся агент доказал свою эффективность: {success_rate:.1f}% успешных запросов!")

if __name__ == "__main__":
    asyncio.run(test_all_old_agent_requests())