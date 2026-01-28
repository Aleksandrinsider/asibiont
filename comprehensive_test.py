#!/usr/bin/env python3
"""
Комплексный тест всех функций AI-ассистента.
Тестирует каждую команду, выявляет ошибки и плохие ответы.
"""

import asyncio
import sys
import os
import json
import re
from datetime import datetime, timezone
import pytz

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_integration.chat import chat_with_ai
from models import Session, Task, User
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ComprehensiveTester:
    """Комплексный тестер всех функций AI"""

    def __init__(self):
        self.user_id = 200012  # Тестовый пользователь
        self.test_results = []
        self.errors_found = []
        self.poor_responses = []

    def log_result(self, test_name, success, details=""):
        """Логирование результата теста"""
        result = {
            'test': test_name,
            'success': success,
            'details': details,
            'timestamp': datetime.now().isoformat()
        }
        self.test_results.append(result)

        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status} {test_name}")
        if details:
            print(f"   {details}")

    def log_error(self, test_name, error, context=""):
        """Логирование ошибки"""
        error_info = {
            'test': test_name,
            'error': str(error),
            'context': context,
            'timestamp': datetime.now().isoformat()
        }
        self.errors_found.append(error_info)
        print(f"🚨 ERROR in {test_name}: {error}")
        if context:
            print(f"   Context: {context}")

    def log_poor_response(self, test_name, response, reason):
        """Логирование плохого ответа"""
        poor_info = {
            'test': test_name,
            'response': response,
            'reason': reason,
            'timestamp': datetime.now().isoformat()
        }
        self.poor_responses.append(poor_info)
        print(f"⚠️  POOR RESPONSE in {test_name}: {reason}")

    async def test_command(self, test_name, message, expected_tool=None, context=None):
        """Тестирование конкретной команды"""
        try:
            print(f"\n🧪 Testing: {test_name}")
            print(f"   Message: {message}")

            result = await chat_with_ai(
                message=message,
                context=context or [],
                user_id=self.user_id
            )

            response = result['response']
            tool_calls = result['tool_calls']

            print(f"   Response: {response[:100]}{'...' if len(response) > 100 else ''}")
            print(f"   Tool calls: {len(tool_calls)}")

            # Анализ tool calls
            if expected_tool and not tool_calls:
                self.log_poor_response(test_name, response, f"Expected tool '{expected_tool}' but no tools called")
                return False

            if expected_tool and tool_calls:
                called_tools = [tc['function'] for tc in tool_calls]
                if expected_tool not in called_tools:
                    self.log_poor_response(test_name, response, f"Expected tool '{expected_tool}' but called: {called_tools}")
                    return False

            # Анализ ответа
            if self._is_poor_response(response):
                self.log_poor_response(test_name, response, "Response quality issues detected")
                return False

            self.log_result(test_name, True, f"Tool calls: {len(tool_calls)}")
            return True

        except Exception as e:
            self.log_error(test_name, e, f"Message: {message}")
            return False

    def _is_poor_response(self, response):
        """Проверка качества ответа"""
        if not response or len(response.strip()) < 5:
            return True

        # Проверка на технические детали в ответе
        if any(phrase in response.lower() for phrase in [
            "error:", "exception:", "traceback:",
            "internal server error", "500 error"
        ]):
            return True

        # Проверка на повторяющиеся фразы
        words = response.lower().split()
        if len(words) > 20 and len(set(words)) / len(words) < 0.6:
            return True

        return False

    async def run_all_tests(self):
        """Запуск всех тестов"""

        print("🚀 НАЧАЛО КОМПЛЕКСНОГО ТЕСТИРОВАНИЯ")
        print("=" * 60)

        # Тесты создания задач
        await self.test_command(
            "add_task_simple",
            "напомни мне купить хлеб завтра в 9 утра",
            "add_task"
        )

        await self.test_command(
            "add_task_complex",
            "создай задачу: подготовить презентацию для клиента, дедлайн послезавтра в 15:00",
            "add_task"
        )

        await self.test_command(
            "add_task_minimal",
            "встреча с командой в 14:30",
            "add_task"
        )

        # Тесты завершения задач
        await self.test_command(
            "complete_task_simple",
            "я выполнил задачу проверить почту",
            "complete_task"
        )

        await self.test_command(
            "complete_task_variations",
            "сделал отчет для клиента",
            "complete_task"
        )

        await self.test_command(
            "complete_task_finished",
            "закончил презентацию",
            "complete_task"
        )

        # Тесты просмотра задач
        await self.test_command(
            "list_tasks_active",
            "покажи мои задачи",
            "list_tasks"
        )

        await self.test_command(
            "list_tasks_completed",
            "покажи выполненные задачи",
            "list_tasks"
        )

        # Тесты изменения задач
        await self.test_command(
            "reschedule_task",
            "перенеси встречу с командой на завтра в 16:00",
            "reschedule_task"
        )

        await self.test_command(
            "edit_task_title",
            "измени название задачи 'купить хлеб' на 'купить продукты'",
            "edit_task"
        )

        # Тесты удаления задач
        await self.test_command(
            "delete_task_simple",
            "удали задачу про встречу",
            "delete_task"
        )

        # Тесты повторяющихся задач
        await self.test_command(
            "set_recurring_daily",
            "напоминай о зарядке каждый день в 8:00",
            "set_recurring_task"
        )

        await self.test_command(
            "set_recurring_weekly",
            "проверяй почту каждую неделю по понедельникам",
            "set_recurring_task"
        )

        # Тесты делегирования
        await self.test_command(
            "delegate_task",
            "делегируй задачу 'подготовить отчет' пользователю ivan",
            "delegate_task"
        )

        # Тесты профиля
        await self.test_command(
            "update_profile",
            "обнови мой профиль: город Москва, компания Google, должность разработчик",
            "update_profile"
        )

        # Тесты поиска партнеров
        await self.test_command(
            "find_partners",
            "найди партнеров по интересам",
            "find_partners"
        )

        # Тесты памяти
        await self.test_command(
            "update_memory",
            "запомни что я предпочитаю чай кофе",
            "update_user_memory"
        )

        # Тесты получения деталей задач
        await self.test_command(
            "get_task_details",
            "покажи детали задачи про презентацию",
            "get_task_details"
        )

        # Тесты опасных операций
        await self.test_command(
            "delete_all_tasks",
            "удали все мои задачи",
            "delete_all_tasks"
        )

        # Тесты диалога (без ожидаемых tool calls)
        await self.test_command(
            "conversation_greeting",
            "привет, как дела?",
            None
        )

        await self.test_command(
            "conversation_help",
            "что ты умеешь?",
            None
        )

        await self.test_command(
            "conversation_unknown",
            "расскажи анекдот",
            None
        )

        # Итоговый отчет
        self._print_report()

    def _print_report(self):
        """Печать итогового отчета"""

        print("\n" + "=" * 60)
        print("📊 ИТОГОВЫЙ ОТЧЕТ ТЕСТИРОВАНИЯ")
        print("=" * 60)

        total_tests = len(self.test_results)
        passed_tests = sum(1 for r in self.test_results if r['success'])
        failed_tests = total_tests - passed_tests

        print(f"Всего тестов: {total_tests}")
        print(f"Пройдено: {passed_tests} ({passed_tests/total_tests*100:.1f}%)")
        print(f"Провалено: {failed_tests} ({failed_tests/total_tests*100:.1f}%)")

        if self.errors_found:
            print(f"\n🚨 ОШИБКИ ({len(self.errors_found)}):")
            for error in self.errors_found:
                print(f"   • {error['test']}: {error['error']}")

        if self.poor_responses:
            print(f"\n⚠️  ПРОБЛЕМНЫЕ ОТВЕТЫ ({len(self.poor_responses)}):")
            for poor in self.poor_responses:
                print(f"   • {poor['test']}: {poor['reason']}")

        print(f"\n{'🎉 ТЕСТИРОВАНИЕ ЗАВЕРШЕНО УСПЕШНО!' if failed_tests == 0 else '⚠️  НАЙДЕНЫ ПРОБЛЕМЫ, ТРЕБУЕТСЯ ИСПРАВЛЕНИЕ'}")

async def main():
    """Главная функция"""
    tester = ComprehensiveTester()
    await tester.run_all_tests()

if __name__ == "__main__":
    asyncio.run(main())