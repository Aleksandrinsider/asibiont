import asyncio
import sys
import os
sys.path.append(os.path.dirname(__file__))

from ai_integration.autonomous_agent import chat_with_ai
from ai_integration.prompts import get_extended_system_prompt
from ai_integration.memory import LongTermMemory
from models import User, SessionLocal

async def test_final_universality():
    print("🎯 ФИНАЛЬНЫЙ ТЕСТ УНИВЕРСАЛЬНОСТИ С КРАТКИМИ ОТВЕТАМИ\n")

    # Create test user
    user = User(
        id=1,
        telegram_id=123456789,
        username="test_user",
        subscription_tier="STANDARD",
        created_at="2024-01-01"
    )

    # Initialize memory
    memory = LongTermMemory(user.id)

    # Get system prompt
    try:
        from datetime import datetime
        current_time = datetime.now()
        system_prompt = get_extended_system_prompt(
            user_now=current_time,
            current_time_str=current_time.strftime("%H:%M"),
            current_date_str=current_time.strftime("%Y-%m-%d"),
            user_username=user.username,
            mentions_str="",
            user_memory=memory,
            user_id_param=user.id
        )
        print(f"✓ Системный промпт готов ({len(system_prompt)} символов)")
    except Exception as e:
        print(f"✗ Ошибка промпта: {e}")
        return

    # Test scenarios
    scenarios = {
        "🍳 Кулинария": [
            "Как приготовить пасту карбонара?",
            "Рецепт салата Цезарь"
        ],
        "🎬 Развлечения": [
            "Какие фильмы посмотреть на выходных?",
            "Рекомендуй музыку для пробежки"
        ],
        "🧹 Бытовые дела": [
            "Как быстро убраться в квартире?",
            "Советы по стирке"
        ],
        "💼 Работа/Бизнес": [
            "Как составить резюме разработчика?",
            "Идеи для стартапа в IT"
        ],
        "🏃 Здоровье/Спорт": [
            "Упражнения для дома без оборудования",
            "Как правильно питаться для набора массы"
        ],
        "🤝 Социальные связи": [
            "Как познакомиться с людьми в новом городе?",
            "Идеи для романтического свидания"
        ]
    }

    total_tests = sum(len(queries) for queries in scenarios.values())
    successful_tests = 0

    print(f"📊 Всего тестов: {total_tests}\n")

    for category, queries in scenarios.items():
        print(f"🎯 {category}")
        category_success = 0

        for query in queries:
            print(f"   ❓ {query}")

            try:
                session = SessionLocal()
                result = await chat_with_ai(
                    message=query,
                    user_id=user.id,
                    db_session=session
                )

                response = result['response']
                response_length = len(response)

                # Check criteria - focus on quality, not length
                has_actionable = any(word in response.lower() for word in [
                    'сделай', 'начни', 'попробуй', 'рекомендую', 'советую', 'план',
                    'шаги', 'этапы', 'вариант', 'идея', 'предлагаю', 'можешь'
                ])
                uses_tools = len(result.get('tool_calls', [])) > 0
                has_concrete_info = len([word for word in response.split() if len(word) > 4]) > 5  # Substantial content
                not_generic = not all(phrase in response.lower() for phrase in ['вообще', 'в принципе', 'как правило'])

                success = has_actionable and has_concrete_info and not_generic

                if success:
                    tool_status = " + инструменты" if uses_tools else ""
                    print(f"      ✅ ПОЛЕЗНЫЙ ОТВЕТ ({len(response)} симв){tool_status}")
                    category_success += 1
                    successful_tests += 1
                else:
                    issues = []
                    if not has_actionable: issues.append("нет конкретных советов")
                    if not has_concrete_info: issues.append("слишком общий")
                    if not_generic is False: issues.append("шаблонный")
                    print(f"      ⚠️ {len(response)} симв - {', '.join(issues)}")

                session.close()

            except Exception as e:
                print(f"      ✗ Ошибка: {e}")

        print(f"   📈 {category}: {category_success}/{len(queries)} успешных\n")

    # Final results
    success_rate = (successful_tests / total_tests) * 100
    print(f"🎉 ФИНАЛЬНЫЙ РЕЗУЛЬТАТ: {successful_tests}/{total_tests} ({success_rate:.1f}%)")

    if success_rate >= 80:
        print("🏆 ОТЛИЧНО! ASI Biont готов к продакшену как универсальный помощник!")
    elif success_rate >= 60:
        print("👍 ХОРОШО! Есть над чем работать, но основа крепкая.")
    else:
        print("🔧 НУЖНЫ ДОРАБОТКИ - слишком много некачественных ответов.")

if __name__ == "__main__":
    asyncio.run(test_final_universality())