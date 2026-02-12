#!/usr/bin/env python3
"""
Комплексный тест логики выбора инструментов для всех тарифов
Тестирование правильности фильтрации инструментов по тарифам
"""
import asyncio
import sys
import os
import json
from datetime import datetime

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.autonomous_agent import chat_with_ai
from ai_integration.dynamic_tools import tool_discovery

class ToolLogicTester:
    """Тестер логики выбора инструментов для разных тарифов"""

    def __init__(self):
        self.tiers = ["LIGHT", "STANDARD", "PREMIUM"]
        self.test_results = {}

    async def test_tool_filtering(self):
        """Тестирование фильтрации инструментов по тарифам"""

        print("🔧 ТЕСТИРОВАНИЕ ФИЛЬТРАЦИИ ИНСТРУМЕНТОВ ПО ТАРИФАМ")
        print("=" * 60)

        for tier in self.tiers:
            print(f"\n🧪 Тестирование тарифа {tier.upper()}")
            print("-" * 40)

            # Получаем доступные инструменты для тарифа
            available_tools = tool_discovery.get_available_tools_for_tier(tier)
            tool_names = [t['function']['name'] for t in available_tools]

            print(f"📊 Доступно инструментов: {len(available_tools)}")
            print(f"🔧 Список: {', '.join(tool_names[:10])}{'...' if len(tool_names) > 10 else ''}")

            # Проверяем наличие ключевых инструментов
            key_tools = {
                'LIGHT': ['list_tasks', 'add_task', 'find_partners', 'show_profile'],
            'STANDARD': ['research_topic', 'delegate_task', 'analyze_tasks'],

            tier_key_tools = key_tools.get(tier, [])
            found_tools = [tool for tool in tier_key_tools if tool in tool_names]
            missing_tools = [tool for tool in tier_key_tools if tool not in tool_names]

            print(f"✅ Найдено ключевых инструментов: {len(found_tools)}/{len(tier_key_tools)}")
            if found_tools:
                print(f"   Доступны: {', '.join(found_tools)}")
            if missing_tools:
                print(f"❌ Отсутствуют: {', '.join(missing_tools)}")

            # Проверяем отсутствие инструментов из высших тарифов
            premium_only = ['set_contact_alert', 'set_activity_alert', 'set_content_strategy', 'toggle_autonomous_feature', 'generate_marketing_content', 'publish_to_telegram', 'analyze_group_opportunities']
            forbidden_tools = []

            if tier == 'LIGHT':
                forbidden_tools = premium_only + ['research_topic', 'generate_marketing_content', 'delegate_task']
            elif tier == 'STANDARD':
                forbidden_tools = ['set_contact_alert', 'set_activity_alert', 'set_content_strategy', 'toggle_autonomous_feature']

            found_forbidden = [tool for tool in forbidden_tools if tool in tool_names]
            if found_forbidden:
                print(f"❌ НЕДОПУСТИМО: Найдены инструменты высших тарифов: {', '.join(found_forbidden)}")
            else:
                print(f"✅ Корректно: Инструменты высших тарифов отсутствуют")

            self.test_results[tier] = {
                'total_tools': len(available_tools),
                'tool_names': tool_names,
                'key_tools_found': len(found_tools),
                'key_tools_total': len(tier_key_tools),
                'forbidden_found': len(found_forbidden)
            }

        return self.test_results

    async def test_ai_tool_selection(self):
        """Тестирование выбора инструментов AI для разных тарифов"""

        print(f"\n🤖 ТЕСТИРОВАНИЕ ВЫБОРА ИНСТРУМЕНТОВ AI")
        print("=" * 60)

        # Тестовые сценарии для каждого тарифа
        test_scenarios = {
            'LIGHT': [
                ("Привет!", "LIGHT", "list_tasks"),
                ("Найди партнеров по Python", "LIGHT", "find_partners"),
                ("Покажи профиль", "LIGHT", "show_profile"),
                ("Создать задачу на завтра", "LIGHT", "add_task"),
            ],
            'STANDARD': [
                ("Привет!", "STANDARD", "list_tasks"),
                ("Найди партнеров по Python", "STANDARD", "find_partners"),
                ("Проанализируй рынок AI", "STANDARD", "research_topic"),
                ("Создать маркетинговый пост", "STANDARD", "generate_marketing_content"),
                ("Делегировать задачу", "STANDARD", "delegate_task"),
            ],
            'PREMIUM': [
                ("Привет!", "PREMIUM", "list_tasks"),
                ("Найди партнеров по Python", "PREMIUM", "find_partners"),
                ("Проанализируй рынок AI", "PREMIUM", "research_topic"),
                ("Создать маркетинговый пост", "PREMIUM", "generate_marketing_content"),
                ("Настроить алерты контактов", "PREMIUM", "set_contact_alert"),
                ("Планировать стратегию бизнеса", "PREMIUM", "research_and_plan"),
            ]
        }

        ai_test_results = {}

        for tier, scenarios in test_scenarios.items():
            print(f"\n🧪 Тестирование AI для тарифа {tier.upper()}")
            print("-" * 40)

            tier_results = []
            user_ids = {
                'LIGHT': 77777,
                'STANDARD': 88888,
                'PREMIUM': 99999
            }

            for message, expected_tier, expected_tool in scenarios:
                print(f"📝 Тест: '{message}' (ожид. инструмент: {expected_tool})")

                try:
                    # Создаем пользователя с нужным тарифом
                    from models import Session, User
                    session = Session()

                    user_id = user_ids[tier]
                    user = session.query(User).filter_by(telegram_id=user_id).first()
                    if not user:
                        # Создаем тестового пользователя
                        user = User(
                            telegram_id=user_id,
                            username=f"test_{tier.lower()}",
                            subscription_tier=tier
                        )
                        session.add(user)
                        session.commit()

                    # Тестируем ответ AI
                    response = await chat_with_ai(message, user_id=user_id)

                    # Проверяем, вызвал ли AI ожидаемый инструмент
                    tools_used = response.get('tools_used', [])
                    tool_calls = response.get('tool_calls', [])

                    tool_called = expected_tool in tools_used
                    print(f"   Результат: {'✅' if tool_called else '❌'} {'Вызван' if tool_called else 'Не вызван'} {expected_tool}")

                    if not tool_called and tool_calls:
                        actual_tools = [tc.get('function', {}).get('name', 'unknown') for tc in tool_calls]
                        print(f"   Вместо этого вызваны: {', '.join(actual_tools)}")

                    tier_results.append({
                        'message': message,
                        'expected_tool': expected_tool,
                        'tool_called': tool_called,
                        'tools_used': tools_used,
                        'response_length': len(response.get('response', ''))
                    })

                except Exception as e:
                    print(f"   Ошибка: {e}")
                    tier_results.append({
                        'message': message,
                        'expected_tool': expected_tool,
                        'error': str(e)
                    })

                # Небольшая пауза между тестами
                await asyncio.sleep(1)

            ai_test_results[tier] = tier_results

            # Статистика для тарифа
            successful = sum(1 for r in tier_results if r.get('tool_called', False))
            total = len(tier_results)
            print(f"📊 Результаты для {tier}: {successful}/{total} правильных вызовов инструментов")

        return ai_test_results

    async def run_comprehensive_test(self):
        """Запуск комплексного тестирования"""

        print("🚀 НАЧИНАЕМ КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ ЛОГИКИ ИНСТРУМЕНТОВ")
        print("=" * 80)

        # Тест 1: Фильтрация инструментов
        filtering_results = await self.test_tool_filtering()

        # Тест 2: Выбор инструментов AI
        ai_results = await self.test_ai_tool_selection()

        # Анализ результатов
        print(f"\n{'='*60}")
        print("📊 АНАЛИЗ РЕЗУЛЬТАТОВ")
        print('='*60)

        # Проверяем фильтрацию
        filtering_ok = True
        for tier, results in filtering_results.items():
            if results['forbidden_found'] > 0:
                print(f"❌ {tier}: Найдены запрещенные инструменты ({results['forbidden_found']})")
                filtering_ok = False
            if results['key_tools_found'] < results['key_tools_total']:
                print(f"⚠️ {tier}: Отсутствуют ключевые инструменты ({results['key_tools_found']}/{results['key_tools_total']})")
                filtering_ok = False

        if filtering_ok:
            print("✅ ФИЛЬТРАЦИЯ ИНСТРУМЕНТОВ: Все тарифы корректно настроены")

        # Проверяем AI выбор инструментов
        ai_ok = True
        for tier, results in ai_results.items():
            successful = sum(1 for r in results if r.get('tool_called', False))
            total = len(results)
            success_rate = successful / total if total > 0 else 0

            if success_rate < 0.7:  # Менее 70% правильных вызовов
                print(f"⚠️ {tier}: Низкая точность выбора инструментов ({successful}/{total} = {success_rate:.1f})")
                ai_ok = False

        if ai_ok:
            print("✅ ВЫБОР ИНСТРУМЕНТОВ AI: Хорошая точность для всех тарифов")

        # Общий вердикт
        overall_success = filtering_ok and ai_ok
        print(f"\n🏆 ОБЩИЙ РЕЗУЛЬТАТ: {'✅ УСПЕХ' if overall_success else '❌ ТРЕБУЕТСЯ ДОРАБОТКА'}")

        if not overall_success:
            print("\n💡 РЕКОМЕНДАЦИИ:")
            if not filtering_ok:
                print("   - Проверить настройку фильтрации инструментов по тарифам")
            if not ai_ok:
                print("   - Улучшить промпты для более точного выбора инструментов AI")
                print("   - Добавить больше примеров использования инструментов в промптах")

        # Сохраняем результаты
        comprehensive_results = {
            'filtering_test': filtering_results,
            'ai_selection_test': ai_results,
            'overall_success': overall_success,
            'timestamp': datetime.now().isoformat()
        }

        with open('tool_logic_test_results.json', 'w', encoding='utf-8') as f:
            json.dump(comprehensive_results, f, ensure_ascii=False, indent=2)

        print(f"\n💾 РЕЗУЛЬТАТЫ СОХРАНЕНЫ В: tool_logic_test_results.json")

        return comprehensive_results

async def main():
    """Запуск комплексного тестирования"""

    # Инициализируем систему инструментов
    from ai_integration import handlers
    tool_discovery.discover_tools_from_module(handlers)

    tester = ToolLogicTester()
    results = await tester.run_comprehensive_test()

if __name__ == "__main__":
    asyncio.run(main())