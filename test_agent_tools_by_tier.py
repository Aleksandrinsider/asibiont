#!/usr/bin/env python3
"""
Тест использования инструментов агентом в зависимости от тарифа
"""
import asyncio
import sys
import os

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.autonomous_agent import chat_with_ai
from ai_integration.tools import get_available_tools
from models import Session, User, SubscriptionTier

async def test_agent_tools_by_tier():
    """Тестирование использования инструментов агентом для разных тарифов"""

    print("🧪 Тестирование использования инструментов агентом по тарифам...")

    # Создаем тестовых пользователей с разными тарифами
    session = Session()
    try:
        # Создаем пользователей если не существуют
        tiers = [
            (11111, SubscriptionTier.LIGHT),
            (22222, SubscriptionTier.STANDARD),
            (33333, SubscriptionTier.PREMIUM)
        ]

        for user_id, tier in tiers:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                user = User(
                    telegram_id=user_id,
                    username=f"test_{tier.value.lower()}",
                    first_name=f"Test {tier.value}",
                    subscription_tier=tier
                )
                session.add(user)
                print(f"✅ Создан пользователь {user_id} с тарифом {tier.value}")
            else:
                user.subscription_tier = tier
                print(f"✅ Обновлен пользователь {user_id} на тариф {tier.value}")

        session.commit()

        # Тестируем каждого пользователя
        for user_id, tier in tiers:
            print(f"\n🎯 Тестирование пользователя {user_id} ({tier.value})")

            # Показываем доступные инструменты
            available_tools = get_available_tools(tier)
            tool_names = [t['function']['name'] for t in available_tools]
            print(f"📋 Доступно инструментов: {len(available_tools)}")
            print(f"🔧 Примеры: {tool_names[:5]}...")

            # Тест приветствия (должен автоматически вызывать инструменты)
            print("\n💬 Тест приветствия:")
            response = await chat_with_ai("Привет", user_id=user_id)
            print(f"Ответ: {response['response'][:200]}...")
            print(f"Вызванные инструменты: {len(response.get('tool_calls', []))}")
            if response.get('tool_calls'):
                called_tools = [tc['function']['name'] for tc in response['tool_calls']]
                print(f"Инструменты: {called_tools}")

            # Тест запроса новостей
            print("\n📰 Тест запроса новостей:")
            response = await chat_with_ai("Что нового в AI?", user_id=user_id)
            print(f"Ответ: {response['response'][:200]}...")
            print(f"Вызванные инструменты: {len(response.get('tool_calls', []))}")
            if response.get('tool_calls'):
                called_tools = [tc['function']['name'] for tc in response['tool_calls']]
                print(f"Инструменты: {called_tools}")

    finally:
        session.close()

    print("\n✅ Тестирование завершено!")

if __name__ == "__main__":
    asyncio.run(test_agent_tools_by_tier())