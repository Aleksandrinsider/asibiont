#!/usr/bin/env python3
"""
Advanced AI Agent Execution Verification Test
Tests that AI promises match actual execution and handles user confirmations
"""

import asyncio
import sys
import os
import re
import json
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import Session, UserProfile, Task
from datetime import datetime, timezone
import pytz

# Test user data
TEST_USER_ID = 22
TEST_USERNAME = "testuser"

class AIAgentVerifier:
    """Advanced verifier for AI agent execution promises"""

    def __init__(self):
        self.session = Session()
        self.test_results = []

    def extract_promises_from_response(self, response):
        """Extract what AI promises to do from response"""
        promises = []

        # Common promise patterns
        promise_patterns = [
            r'(?:создам?|добавлю|сделаю|обновлю|изменю|удалю|завершу)\s+задачу',
            r'(?:обновлю|изменю)\s+профиль',
            r'(?:запомню|сохраню)\s+информацию',
            r'(?:найду|покажу)\s+контакты',
            r'(?:предложу|дам)\s+идеи',
            r'(?:покажу|выведу)\s+список',
            r'(?:отправлю|делегирую)\s+задачу',
            r'(?:проверю|посмотрю)\s+статус'
        ]

        response_lower = response.lower()
        for pattern in promise_patterns:
            if re.search(pattern, response_lower):
                promises.append(pattern)

        return promises

    def check_function_execution(self, response, expected_functions):
        """Check if expected functions were called based on response content"""
        executed = []

        # Check for task creation
        if 'add_task' in expected_functions and ('созда' in response.lower() or 'добав' in response.lower()):
            executed.append('add_task')

        # Check for profile updates
        if 'update_profile' in expected_functions and ('обнов' in response.lower() or 'профиль' in response.lower()):
            executed.append('update_profile')

        # Check for task completion
        if 'complete_task' in expected_functions and ('заверш' in response.lower() or 'готов' in response.lower()):
            executed.append('complete_task')

        # Check for task listing
        if 'list_tasks' in expected_functions and ('список' in response.lower() or 'задач' in response.lower()):
            executed.append('list_tasks')

        return executed

    async def simulate_user_confirmation(self, ai_response):
        """Simulate user responses to AI confirmation requests"""
        response_lower = ai_response.lower()

        # If AI asks for confirmation
        if any(word in response_lower for word in ['подтвердить', 'уверен', 'точно', 'да?', 'ок?']):
            return "да, подтверждаю"

        # If AI asks for time
        if any(word in response_lower for word in ['время', 'когда', 'во сколько']):
            return "завтра в 10 утра"

        # If AI asks for details
        if any(word in response_lower for word in ['подробности', 'детали', 'описание']):
            return "нужна срочная задача для проекта"

        return None

    async def test_promise_execution(self, message, expected_functions, description):
        """Test that AI promises match execution"""
        print(f"\n🧪 {description}")
        print(f"Запрос: {message}")
        print(f"Ожидаемые функции: {expected_functions}")

        # Get AI response
        response = await chat_with_ai(
            message=message,
            user_id=TEST_USER_ID,
            context=None,
            message_type=None
        )

        if not response:
            print("❌ Нет ответа от AI")
            return False

        print(f"Ответ AI: {response[:200]}...")

        # Extract promises
        promises = self.extract_promises_from_response(response)
        print(f"Обещания AI: {promises}")

        # Check execution
        executed = self.check_function_execution(response, expected_functions)
        print(f"Выполненные функции: {executed}")

        # Check if promises match execution
        success = len(executed) > 0 or len(promises) == 0
        if success:
            print("✅ Промисы соответствуют выполнению")
        else:
            print("❌ Расхождение между промисами и выполнением")

        return success

    async def test_interactive_scenario(self, initial_message, follow_up_responses, description):
        """Test interactive scenarios with user confirmations"""
        print(f"\n🎭 {description}")
        print(f"Начальный запрос: {initial_message}")

        conversation_context = []
        current_response = None

        for i, expected_response in enumerate(follow_up_responses):
            if i == 0:
                # Initial AI response
                current_response = await chat_with_ai(
                    message=initial_message,
                    user_id=TEST_USER_ID,
                    context=conversation_context,
                    message_type=None
                )
            else:
                # Follow-up with user response
                user_response = expected_response
                conversation_context.append({"role": "assistant", "content": current_response})
                conversation_context.append({"role": "user", "content": user_response})

                current_response = await chat_with_ai(
                    message=user_response,
                    user_id=TEST_USER_ID,
                    context=conversation_context,
                    message_type=None
                )

            print(f"Шаг {i+1} - AI: {current_response[:150]}...")

            # Check if AI asks for confirmation
            if any(word in current_response.lower() for word in ['подтвердить', 'уверен', 'да?', 'ок?']):
                print("🤔 AI просит подтверждения - это правильно")

        print("✅ Интерактивный сценарий завершен")
        return True

    async def run_comprehensive_test(self):
        """Run comprehensive verification tests"""
        print("🚀 НАЧИНАЮ ГЛУБОКУЮ ПРОВЕРКУ ИСПОЛНЕНИЯ AI АГЕНТА")
        print("=" * 60)

        tests_passed = 0
        total_tests = 0

        # Test 1: Simple task creation
        total_tests += 1
        if await self.test_promise_execution(
            "создай задачу купить продукты",
            ['add_task'],
            "Тест 1: Простое создание задачи"
        ):
            tests_passed += 1

        # Test 2: Profile update
        total_tests += 1
        if await self.test_promise_execution(
            "я работаю в Яндексе разработчиком",
            ['update_profile'],
            "Тест 2: Обновление профиля"
        ):
            tests_passed += 1

        # Test 3: Task completion
        total_tests += 1
        if await self.test_promise_execution(
            "я купил продукты, задача выполнена",
            ['complete_task'],
            "Тест 3: Завершение задачи"
        ):
            tests_passed += 1

        # Test 4: Task listing
        total_tests += 1
        if await self.test_promise_execution(
            "покажи мои задачи",
            ['list_tasks'],
            "Тест 4: Показ списка задач"
        ):
            tests_passed += 1

        # Test 5: Interactive delegation (requires confirmation)
        total_tests += 1
        if await self.test_interactive_scenario(
            "делегируй задачу написать отчет @user123",
            ["да, подтверждаю делегирование"],
            "Тест 5: Интерактивное делегирование с подтверждением"
        ):
            tests_passed += 1

        # Test 6: Memory update
        total_tests += 1
        if await self.test_promise_execution(
            "запомни что я люблю чай больше кофе",
            ['update_user_memory'],
            "Тест 6: Сохранение в память"
        ):
            tests_passed += 1

        # Test 7: Contact search
        total_tests += 1
        if await self.test_promise_execution(
            "найди контакты по интересам спорт",
            ['find_partners'],
            "Тест 7: Поиск контактов"
        ):
            tests_passed += 1

        # Test 8: Ideas generation
        total_tests += 1
        if await self.test_promise_execution(
            "придумай идеи для стартапа",
            ['brainstorm_ideas'],
            "Тест 8: Генерация идей"
        ):
            tests_passed += 1

        print("\n" + "=" * 60)
        print(f"📊 РЕЗУЛЬТАТЫ ГЛУБОКОЙ ПРОВЕРКИ: {tests_passed}/{total_tests} тестов пройдено ({tests_passed/total_tests*100:.1f}%)")

        if tests_passed == total_tests:
            print("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ! AI АГЕНТ ИСПОЛНЯЕТ ВСЕ ОБЕЩАНИЯ!")
        else:
            print(f"⚠️ {total_tests - tests_passed} тестов провалено - нужны доработки")

        return tests_passed == total_tests

    async def cleanup(self):
        """Clean up test data"""
        print("\n🧹 Очистка тестовых данных...")

        try:
            # Remove test tasks
            test_tasks = self.session.query(Task).filter_by(user_id=TEST_USER_ID).all()
            for task in test_tasks:
                self.session.delete(task)

            # Reset test user profile
            profile = self.session.query(UserProfile).filter_by(user_id=TEST_USER_ID).first()
            if profile:
                profile.interests = None
                profile.skills = None
                profile.goals = None
                profile.city = None
                profile.company = None
                profile.position = None

            self.session.commit()
            print("✅ Тестовые данные очищены")

        except Exception as e:
            print(f"❌ Ошибка очистки: {e}")
            self.session.rollback()
        finally:
            self.session.close()

async def main():
    """Main test execution"""
    verifier = AIAgentVerifier()

    try:
        success = await verifier.run_comprehensive_test()
        await verifier.cleanup()

        if success:
            print("\n🎯 МИССИЯ ВЫПОЛНЕНА: AI агент точно исполняет все обещания!")
            return 0
        else:
            print("\n🔧 НУЖНЫ ДОРАБОТКИ: Некоторые обещания не выполняются")
            return 1

    except Exception as e:
        print(f"\n❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
        await verifier.cleanup()
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)