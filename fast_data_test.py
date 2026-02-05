#!/usr/bin/env python3
"""
Быстрый тест сбора данных AI-агентом
Проверяет активный сбор информации о профиле пользователя
"""

import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import UserProfile, Task, Interaction, init_db, get_session
from config import TEST_USER_ID
import logging

# Отключаем лишние логи
logging.getLogger('ai_integration.chat').setLevel(logging.WARNING)
logging.getLogger('ai_integration.utils').setLevel(logging.WARNING)
logging.getLogger('main').setLevel(logging.WARNING)

class FastDataCollectionTester:
    def __init__(self):
        self.user_id = TEST_USER_ID
        self.session = None

    async def setup(self):
        """Быстрая настройка для теста"""
        print("🔧 Настройка быстрого теста...")

        # Инициализируем БД
        await init_db()
        self.session = get_session()

        # Очищаем старые данные
        self.session.query(Interaction).filter_by(user_id=self.user_id).delete()
        self.session.query(Task).filter_by(user_id=self.user_id).delete()
        self.session.query(UserProfile).filter_by(user_id=self.user_id).delete()
        self.session.commit()

        # Создаем пустой профиль
        profile = UserProfile(
            user_id=self.user_id,
            interaction_count=0
        )
        self.session.add(profile)
        self.session.commit()

        print("✅ Настройка завершена")

    async def test_data_collection(self):
        """Тестируем сбор данных"""
        print("\n🧪 ТЕСТ СБОРА ДАННЫХ")
        print("=" * 50)

        test_cases = [
            ("Привет!", "Проверка приветствия и вопросов о профиле"),
            ("Что ты умеешь?", "Проверка вопросов о целях"),
            ("Я из Санкт-Петербурга", "Заполнение города"),
            ("Интересуюсь Python и AI", "Заполнение интересов"),
            ("Умею программировать", "Заполнение навыков"),
            ("Хочу изучить машинное обучение", "Заполнение целей"),
        ]

        results = []

        for message, description in test_cases:
            print(f"\n🔹 {description}")
            print(f"📝 Сообщение: {message}")

            try:
                # Быстрый ответ без лишних данных
                response = await asyncio.wait_for(
                    chat_with_ai(self.user_id, message, use_cache=False),
                    timeout=15  # 15 секунд таймаут
                )

                # Проверяем, задает ли AI вопросы
                asks_questions = self.check_if_asks_questions(response)
                results.append((message, asks_questions))

                print(f"✅ Ответ получен ({len(response)} символов)")
                print(f"❓ Задает вопросы: {'ДА' if asks_questions else 'НЕТ'}")

            except asyncio.TimeoutError:
                print("❌ Таймаут ответа")
                results.append((message, False))
            except Exception as e:
                print(f"❌ Ошибка: {str(e)}")
                results.append((message, False))

        return results

    def check_if_asks_questions(self, response):
        """Проверяет, задает ли AI вопросы о данных"""
        question_indicators = [
            "расскажи", "какие у тебя", "что ты", "где ты",
            "чем занимаешься", "цели на", "интересы", "навыки",
            "проекты", "работаешь", "месяц"
        ]

        response_lower = response.lower()
        return any(indicator in response_lower for indicator in question_indicators)

    async def run(self):
        """Запуск быстрого теста"""
        print("🚀 БЫСТРЫЙ ТЕСТ СБОРА ДАННЫХ AI-АГЕНТОМ")
        print("=" * 60)

        await self.setup()
        results = await self.test_data_collection()

        # Анализ результатов
        print("\n📊 РЕЗУЛЬТАТЫ")
        print("=" * 30)

        successful = sum(1 for _, success in results if success)
        total = len(results)

        for message, success in results:
            status = "✅" if success else "❌"
            print(f"{status} {message}")

        print(f"\n🎯 ИТОГО: {successful}/{total} тестов пройдено")

        if successful >= total * 0.7:  # 70% успех
            print("🎉 AI-агент АКТИВНО собирает данные!")
        else:
            print("⚠️  AI-агент нуждается в доработке")

        # Закрываем сессию
        if self.session:
            self.session.close()

async def main():
    tester = FastDataCollectionTester()
    await tester.run()

if __name__ == "__main__":
    asyncio.run(main())