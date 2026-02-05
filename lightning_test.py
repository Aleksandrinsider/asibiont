#!/usr/bin/env python3
"""
Молниеносный тест - проверка только ключевых функций сбора данных
"""

import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import UserProfile, init_db, get_session
from config import TEST_USER_ID
import logging

# Полностью отключаем логи
logging.getLogger().setLevel(logging.CRITICAL)

class LightningTester:
    def __init__(self):
        self.user_id = TEST_USER_ID

    async def test_key_scenarios(self):
        """Тестируем только ключевые сценарии"""
        print("⚡ МОЛНИЕНОСНЫЙ ТЕСТ СБОРА ДАННЫХ")
        print("=" * 40)

        # Инициализация
        await init_db()
        session = get_session()

        # Очищаем и создаем пустой профиль
        session.query(UserProfile).filter_by(user_id=self.user_id).delete()
        profile = UserProfile(user_id=self.user_id, interaction_count=0)
        session.add(profile)
        session.commit()
        session.close()

        # Ключевые тесты
        tests = [
            ("Привет!", "приветствие"),
            ("Что ты умеешь?", "возможности"),
        ]

        results = []
        for message, test_type in tests:
            print(f"🔹 Тест: {test_type}")
            try:
                response = await asyncio.wait_for(
                    chat_with_ai(self.user_id, message, use_cache=False),
                    timeout=10
                )

                # Быстрая проверка вопросов
                has_questions = any(word in response.lower() for word in [
                    "расскажи", "цели", "интересы", "навыки", "город"
                ])

                status = "✅" if has_questions else "❌"
                print(f"   {status} {len(response)} символов")
                results.append(has_questions)

            except Exception as e:
                print(f"   ❌ Ошибка: {str(e)[:30]}...")
                results.append(False)

        # Результат
        success_rate = sum(results) / len(results) * 100
        print(f"\n🎯 УСПЕХ: {success_rate:.0f}%")
        print("🎉 АКТИВНЫЙ СБОР ДАННЫХ!" if success_rate >= 50 else "⚠️ НУЖНА ДОРАБОТКА")

async def main():
    tester = LightningTester()
    await tester.test_key_scenarios()

if __name__ == "__main__":
    asyncio.run(main())