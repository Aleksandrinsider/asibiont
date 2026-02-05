import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from universal_agent import UniversalAgent
from self_generating_agent import SelfGeneratingAgent

async def compare_agents():
    """Сравнение трех подходов к AI-агентам"""

    print("🤖 СРАВНЕНИЕ ПОДХОДОВ К СОЗДАНИЮ AI-АГЕНТОВ")
    print("=" * 60)

    # Тестовые запросы
    test_requests = [
        "Создай задачу 'встретиться с командой' на послезавтра в 14:00",
        "Покажи список моих задач",
        "Найди партнеров для изучения Python"
    ]

    user_id = 123456789

    # 1. Традиционный подход (имитация)
    print("\n1️⃣ ТРАДИЦИОННЫЙ ПОДХОД (существующий код)")
    print("-" * 40)
    print("❌ Требует 4000+ строк кода")
    print("❌ Ограничен предопределенными командами")
    print("❌ Не может обрабатывать новые запросы")
    print("💡 Пример: жесткий if-else для каждого типа запроса")

    # 2. Tool Calling подход
    print("\n2️⃣ TOOL CALLING ПОДХОД")
    print("-" * 40)

    agent_tools = UniversalAgent()
    for i, request in enumerate(test_requests, 1):
        print(f"\nЗапрос {i}: {request}")
        try:
            response = await agent_tools.process_request(request, user_id)
            print(f"✅ Ответ: {response[:100]}...")
        except Exception as e:
            print(f"❌ Ошибка: {e}")

    # 3. Самогенерация
    print("\n3️⃣ САМОГЕНЕРАЦИЯ")
    print("-" * 40)

    agent_self = SelfGeneratingAgent()
    for i, request in enumerate(test_requests, 1):
        print(f"\nЗапрос {i}: {request}")
        try:
            response = await agent_self.process_request(request, user_id)
            print(f"✅ Ответ: {response[:100]}...")
            print(f"📚 Сгенерировано функций: {len(agent_self.generated_functions)}")
        except Exception as e:
            print(f"❌ Ошибка: {e}")

    # Итоговое сравнение
    print("\n" + "=" * 60)
    print("📊 ИТОГОВОЕ СРАВНЕНИЕ")
    print("=" * 60)

    comparison = """
| Подход              | Код      | Гибкость | Автономность |
|---------------------|----------|----------|-------------|
| Традиционный        | 4000+ ⚠️ | Низкая ❌ | Нет ❌     |
| Tool Calling        | ~100 ✅  | Средняя ⚠️| Частичная ⚠️|
| Самогенерация       | ~10 🏆  | Максимальная 🏆 | Полная 🏆 |
"""

    print(comparison)

    print("\n🎯 ВЫВОД:")
    print("Самогенерация позволяет создавать AI-агентов будущего:")
    print("• Полная автономность")
    print("• Самообучение")
    print("• Решение любых задач")
    print("• Минимальный код для максимальной мощности")

if __name__ == "__main__":
    asyncio.run(compare_agents())