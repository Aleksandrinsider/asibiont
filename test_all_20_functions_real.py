#!/usr/bin/env python3
"""Быстрая проверка всех 20 функций агента"""
import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ai_integration.autonomous_agent import chat_with_ai
import logging

logging.basicConfig(level=logging.WARNING, format='%(message)s')
logger = logging.getLogger(__name__)

class FunctionTester:
    def __init__(self):
        self.user_id = 99999
        self.results = []
        self.total_tests = 0
        self.passed_tests = 0

    def log_test(self, function_name, success, message=""):
        self.total_tests += 1
        if success:
            self.passed_tests += 1
        status = "[PASS]" if success else "[FAIL]"
        result = f"{status} {function_name}: {message}"
        self.results.append(result)
        print(f"{self.total_tests}. {result}")

    async def test_function(self, name, message, expected_func=None):
        """Универсальный тест функции"""
        try:
            result = await chat_with_ai(message, user_id=self.user_id)
            functions = result.get("tools_used", [])
            if expected_func is None:
                expected_func = name
            success = expected_func in functions
            self.log_test(name, success, "OK" if success else f"Called: {functions[:2] if functions else 'none'}")
        except Exception as e:
            self.log_test(name, False, str(e)[:40])

    async def run_all_tests(self):
        print("=" * 60)
        print("ТЕСТ 20 ФУНКЦИЙ АГЕНТА")
        print("=" * 60)

        # Список тестов: (имя_функции, запрос, [ожидаемая_функция])
        tests = [
            ("add_task", "Создай задачу тест завтра 10:00"),
            ("complete_task", "Отметь выполнение задачи тест"),
            ("reschedule_task", "Перенеси тест на завтра 15:00"),
            ("edit_task", "Измени тест - добавь описание важно"),
            ("delete_task", "Удали задачу тест"),
            ("list_tasks", "Покажи задачи"),
            ("find_partners", "Найди партнеров для Python"),
            ("find_relevant_contacts_for_task", "Найди контакты для пробежки"),
            ("delegate_task", "Делегируй тест @test_user"),
            ("get_delegation_progress", "Прогресс делегирования?"),
            ("accept_delegated_task", "Принять тест"),
            ("reject_delegated_task", "Не буду тест"),
            ("update_profile", "Добавь навык Python"),
            ("show_profile", "Покажи профиль"),
            ("update_user_memory", "Запомни что я работаю по утрам"),
            ("get_task_details", "Детали задачи тест"),
            ("generate_marketing_content", "Создай пост про AI"),
            ("set_activity_alert", "Уведомляй о спорте"),
            ("set_contact_alert", "Мониторь @test_user"),
            ("check_topic_relevance", "Актуальна ли тема Web3?"),
        ]

        for name, message, *extra in tests:
            expected = extra[0] if extra else name
            await self.test_function(name, message, expected)

        print("\n" + "=" * 60)
        print("ИТОГО")
        print("=" * 60)
        print(f"Всего: {self.total_tests}")
        print(f"Пройдено: {self.passed_tests}")
        print(f"Провалено: {self.total_tests - self.passed_tests}")
        print(f"Успех: {self.passed_tests / self.total_tests * 100:.1f}%")
        
        if self.passed_tests == self.total_tests:
            print("\nВСЕ ТЕСТЫ ПРОЙДЕНЫ!")
        else:
            print(f"\nТребуется доработка: {self.total_tests - self.passed_tests} функций")

async def main():
    tester = FunctionTester()
    await tester.run_all_tests()

if __name__ == "__main__":
    asyncio.run(main())
