#!/usr/bin/env python3
"""
Comprehensive Agent Testing Script
Tests all agent capabilities with real requests
"""

import asyncio
import sys
import os
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from ai_integration.handlers import get_partners_list, find_relevant_contacts_for_task
from models import User, Task, Subscription, SubscriptionTier, Base
from config import DATABASE_URL
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

class AgentTester:
    def __init__(self):
        self.engine = create_engine(DATABASE_URL)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.test_user = None

    async def process_message(self, message):
        """Process message using chat_with_ai function"""
        session = self.SessionLocal()
        try:
            result = await chat_with_ai(
                message=message,
                user_id=self.test_user.telegram_id,
                db_session=session
            )
            return result.get('response', 'No response')
        finally:
            session.close()

    def setup_test_data(self):
        """Setup test user and data"""
        # Create tables
        Base.metadata.create_all(bind=self.engine)

        session = self.SessionLocal()

        try:
            # Create test user
            self.test_user = User(
                telegram_id=123456789,
                username="test_user",
                first_name="Test",
                memory="Тестовый пользователь для проверки агента"
            )
            session.add(self.test_user)

            # Create subscription
            subscription = Subscription(
                user_id=self.test_user.telegram_id,
                tier_name='LIGHT',
                status='active'
            )
            session.add(subscription)

            # Create some test tasks
            tasks_data = [
                {
                    "title": "Изучить документацию по AI",
                    "description": "Прочитать и понять основные концепции машинного обучения",
                    "due_date": datetime.now() + timedelta(days=1),
                    "priority": "medium"
                },
                {
                    "title": "Подготовить презентацию проекта",
                    "description": "Создать слайды для презентации стартапа инвесторам",
                    "due_date": datetime.now() + timedelta(hours=4),
                    "priority": "high"
                },
                {
                    "title": "Позвонить поставщику",
                    "description": "Обсудить условия поставки комплектующих",
                    "due_date": datetime.now() + timedelta(hours=2),
                    "priority": "high"
                }
            ]

            for task_data in tasks_data:
                task = Task(
                    user_id=self.test_user.telegram_id,
                    **task_data
                )
                session.add(task)

            session.commit()

        except Exception as e:
            session.rollback()
            print(f"Error setting up test data: {e}")
        finally:
            session.close()

    async def test_basic_greeting(self):
        """Test basic greeting response"""
        print("\n=== ТЕСТ 1: Приветствие ===")
        response = await self.process_message("привет")
        print(f"Запрос: 'привет'")
        print(f"Ответ: {response}")
        return response

    async def test_task_creation(self):
        """Test task creation"""
        print("\n=== ТЕСТ 2: Создание задачи ===")
        response = await self.process_message("создай задачу: подготовить отчет по продажам к завтрашнему утру")
        print(f"Запрос: 'создай задачу: подготовить отчет по продажам к завтрашнему утру'")
        print(f"Ответ: {response}")
        return response

    async def test_task_listing(self):
        """Test task listing"""
        print("\n=== ТЕСТ 3: Просмотр задач ===")
        response = await self.process_message("покажи мои задачи")
        print(f"Запрос: 'покажи мои задачи'")
        print(f"Ответ: {response}")
        return response

    async def test_task_completion(self):
        """Test task completion"""
        print("\n=== ТЕСТ 4: Завершение задачи ===")
        response = await self.process_message("я закончил подготовку презентации")
        print(f"Запрос: 'я закончил подготовку презентации'")
        print(f"Ответ: {response}")
        return response

    async def test_task_reschedule(self):
        """Test task rescheduling"""
        print("\n=== ТЕСТ 5: Перенос задачи ===")
        response = await self.process_message("перенеси задачу 'Изучить документацию по AI' на послезавтра")
        print(f"Запрос: 'перенеси задачу 'Изучить документацию по AI' на послезавтра'")
        print(f"Ответ: {response}")
        return response

    async def test_profile_update(self):
        """Test profile update"""
        print("\n=== ТЕСТ 6: Обновление профиля ===")
        response = await self.process_message("обнови мой профиль: город Санкт-Петербург, навыки Python, SQL, React")
        print(f"Запрос: 'обнови мой профиль: город Санкт-Петербург, навыки Python, SQL, React'")
        print(f"Ответ: {response}")
        return response

    async def test_memory_update(self):
        """Test memory update"""
        print("\n=== ТЕСТ 7: Запоминание информации ===")
        response = await self.process_message("запомни что я предпочитаю работать по утрам с 9 до 12")
        print(f"Запрос: 'запомни что я предпочитаю работать по утрам с 9 до 12'")
        print(f"Ответ: {response}")
        return response

    async def test_partner_search(self):
        """Test partner search"""
        print("\n=== ТЕСТ 8: Поиск партнеров ===")
        response = await self.process_message("найди единомышленников для разработки AI проектов")
        print(f"Запрос: 'найди единомышленников для разработки AI проектов'")
        print(f"Ответ: {response}")
        return response

    async def test_task_delegation(self):
        """Test task delegation"""
        print("\n=== ТЕСТ 9: Делегирование задачи ===")
        response = await self.process_message("делегируй задачу 'Позвонить поставщику' кому-нибудь")
        print(f"Запрос: 'делегируй задачу 'Позвонить поставщику' кому-нибудь'")
        print(f"Ответ: {response}")
        return response

    async def test_task_analysis(self):
        """Test task analysis"""
        print("\n=== ТЕСТ 10: Анализ задач ===")
        response = await self.process_message("что мне делать сейчас")
        print(f"Запрос: 'что мне делать сейчас'")
        print(f"Ответ: {response}")
        return response

    async def test_complex_request(self):
        """Test complex multi-step request"""
        print("\n=== ТЕСТ 11: Сложный запрос ===")
        response = await self.process_message("я хочу найти партнеров для стартапа в сфере edtech, создать план развития и запланировать встречу")
        print(f"Запрос: 'я хочу найти партнеров для стартапа в сфере edtech, создать план развития и запланировать встречу'")
        print(f"Ответ: {response}")
        return response

    async def test_weather_integration(self):
        """Test weather integration"""
        print("\n=== ТЕСТ 12: Интеграция погоды ===")
        response = await self.process_message("какая погода сегодня, может организуем пробежку?")
        print(f"Запрос: 'какая погода сегодня, может организуем пробежку?'")
        print(f"Ответ: {response}")
        return response

    async def run_all_tests(self):
        """Run all tests"""
        print("🚀 НАЧИНАЕМ КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ АГЕНТА")
        print("=" * 60)

        # Setup
        self.setup_test_data()
        # await self.init_chatbot()  # Removed

        # Run tests
        tests = [
            self.test_basic_greeting,
            self.test_task_creation,
            self.test_task_listing,
            self.test_task_completion,
            self.test_task_reschedule,
            self.test_profile_update,
            self.test_memory_update,
            self.test_partner_search,
            self.test_task_delegation,
            self.test_task_analysis,
            self.test_complex_request,
            self.test_weather_integration
        ]

        results = []
        for test in tests:
            try:
                result = await test()
                results.append((test.__name__, "✅", result))
            except Exception as e:
                print(f"❌ Ошибка в {test.__name__}: {e}")
                results.append((test.__name__, "❌", str(e)))

        # Summary
        print("\n" + "=" * 60)
        print("📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ:")
        print("=" * 60)

        passed = sum(1 for _, status, _ in results if status == "✅")
        total = len(results)

        for test_name, status, _ in results:
            print(f"{status} {test_name}")

        print(f"\n🎯 ИТОГО: {passed}/{total} тестов пройдено успешно")

        if passed == total:
            print("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ! АГЕНТ РАБОТАЕТ КОРРЕКТНО")
        else:
            print("⚠️  НЕКОТОРЫЕ ТЕСТЫ ПРОВАЛИЛИСЬ - ТРЕБУЕТСЯ ДОРАБОТКА")

async def main():
    tester = AgentTester()
    await tester.run_all_tests()

if __name__ == "__main__":
    asyncio.run(main())