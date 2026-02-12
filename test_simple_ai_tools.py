#!/usr/bin/env python3
"""
Простой тест выбора инструментов AI без базы данных
Использует мок-данные для проверки логики выбора инструментов
"""
import asyncio
import sys
import os
import json
from unittest.mock import patch, MagicMock

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai

class MockUser:
    """Мок пользователь для тестирования"""
    def __init__(self, telegram_id, subscription_tier):
        self.id = telegram_id
        self.telegram_id = telegram_id
        self.subscription_tier = subscription_tier
        self.username = f"test_{subscription_tier.lower()}"
        self.city = "Москва"
        self.skills = "Python, AI"
        self.interests = "разработка, стартапы"
        self.timezone = "Europe/Moscow"
        self.long_term_memory = []
        self.conversation_context = []

class ToolSelectionTester:
    """Тестер выбора инструментов AI"""

    def __init__(self):
        pass  # Не нужен агент, используем функцию напрямую

    async def test_ai_tool_selection_simple(self):
        """Простой тест выбора инструментов AI"""

        print("🤖 ТЕСТИРОВАНИЕ ВЫБОРА ИНСТРУМЕНТОВ AI (ПРОСТОЙ ТЕСТ)")
        print("=" * 60)

        # Тестовые сценарии
        test_scenarios = [
            ("LIGHT", 77777, "Привет!", ["list_tasks"]),
            ("LIGHT", 77777, "Найди партнеров по Python", ["find_partners"]),
            ("STANDARD", 88888, "Проанализируй рынок AI", ["research_topic"]),
            ("PREMIUM", 99999, "Настроить алерты контактов", ["set_contact_alert"]),
        ]

        results = {}

        for tier, user_id, message, expected_tools in test_scenarios:
            print(f"\n🧪 {tier}: '{message}'")
            print("-" * 40)

            try:
                # Мокаем базу данных
                mock_user = MockUser(user_id, tier)
                mock_session = MagicMock()
                mock_query = MagicMock()
                mock_query.filter_by.return_value.first.return_value = mock_user
                mock_session.query.return_value = mock_query

                with patch('ai_integration.autonomous_agent.Session', return_value=mock_session), \
                     patch('models.Session', return_value=mock_session):

                    # Тестируем ответ AI
                    response = await chat_with_ai(message, user_id=user_id)

                # Проверяем, вызвал ли AI ожидаемый инструмент
                tools_used = response.get('tools_used', [])
                tool_calls = response.get('tool_calls', [])

                success = any(tool in tools_used for tool in expected_tools)
                print(f"   Ожидаемые инструменты: {expected_tools}")
                print(f"   Вызванные инструменты: {tools_used}")
                print(f"   Результат: {'✅' if success else '❌'} {'УСПЕХ' if success else 'ПРОВАЛ'}")

                results[f"{tier}_{message[:20]}"] = {
                    'success': success,
                    'expected': expected_tools,
                    'used': tools_used,
                    'response_length': len(response.get('response', ''))
                }

            except Exception as e:
                print(f"   Ошибка: {e}")
                import traceback
                traceback.print_exc()
                results[f"{tier}_{message[:20]}"] = {
                    'success': False,
                    'error': str(e)
                }

        return results

async def main():
    """Запуск простого тестирования"""

    # Инициализируем систему инструментов
    from ai_integration import handlers
    from ai_integration.dynamic_tools import tool_discovery
    tool_discovery.discover_tools_from_module(handlers)

    tester = ToolSelectionTester()
    results = await tester.test_ai_tool_selection_simple()

    # Анализ результатов
    print(f"\n{'='*60}")
    print("📊 АНАЛИЗ РЕЗУЛЬТАТОВ")
    print('='*60)

    successful = sum(1 for r in results.values() if r.get('success', False))
    total = len(results)

    print(f"✅ Успешных тестов: {successful}/{total}")

    if successful >= total * 0.7:  # 70% успех
        print("✅ ВЫБОР ИНСТРУМЕНТОВ AI: Хорошая точность")
        overall_success = True
    else:
        print("❌ ВЫБОР ИНСТРУМЕНТОВ AI: Требуется улучшение")
        overall_success = False

    # Сохраняем результаты
    with open('simple_tool_selection_test_results.json', 'w', encoding='utf-8') as f:
        json.dump({
            'results': results,
            'overall_success': overall_success,
            'timestamp': str(asyncio.get_event_loop().time())
        }, f, ensure_ascii=False, indent=2)

    print(f"\n💾 РЕЗУЛЬТАТЫ СОХРАНЕНЫ В: simple_tool_selection_test_results.json")
    print(f"\n🏆 ОБЩИЙ РЕЗУЛЬТАТ: {'✅ УСПЕХ' if overall_success else '❌ ТРЕБУЕТСЯ ДОРАБОТКА'}")

if __name__ == "__main__":
    asyncio.run(main())