"""
Simple agent demo - shows how agent works with functions and responses
"""

import asyncio
import logging
import os

os.environ['LOCAL'] = '1'
os.environ['DATABASE_URL'] = 'sqlite:///local.db'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def demo():
    """Simple demo of agent capabilities"""
    print("🤖 ДЕМОНСТРАЦИЯ РАБОТЫ АГЕНТА")
    print("=" * 50)

    from ai_integration.autonomous_agent import get_autonomous_agent
    from models import Session

    session = Session()
    agent = get_autonomous_agent()

    # Use existing user for demo
    user_id = 123456789  # Use existing user ID

    demo_messages = [
        "Привет! Создай задачу 'протестировать новый функционал' на завтра в 10:00",
        "Мои навыки: Python, JavaScript, теперь добавь в профиль",
        "Найди партнеров для веб-разработки",
        "Покажи новости про ИИ",
        "Обнови мой профиль - добавь цель 'создать SaaS продукт'"
    ]

    for i, message in enumerate(demo_messages, 1):
        print(f"\n📝 ШАГ {i}: {message}")

        try:
            response = await agent.process_request(
                user_message=message,
                user_id=user_id,
                context=None,
                session=session,
                subscription_tier='PREMIUM'
            )

            print(f"🤖 АГЕНТ: {response[:200]}...")

            # Check if agent called any tools
            if "создал" in response.lower() or "нашел" in response.lower() or "обновил" in response.lower():
                print("✅ АГЕНТ ВЫПОЛНИЛ ФУНКЦИЮ!")
            else:
                print("💬 АГЕНТ ОТВЕТИЛ В ЧАТ")

        except Exception as e:
            print(f"❌ ОШИБКА: {e}")

        print("-" * 30)

    session.close()
    print("\n✅ ДЕМОНСТРАЦИЯ ЗАВЕРШЕНА")

if __name__ == "__main__":
    asyncio.run(demo())