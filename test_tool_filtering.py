#!/usr/bin/env python3
"""
Упрощенный тест фильтрации инструментов по тарифам
Тестирование только логики фильтрации без базы данных
"""
import sys
import os

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.dynamic_tools import tool_discovery

def test_tool_filtering():
    """Тестирование фильтрации инструментов по тарифам"""

    print("🔧 ТЕСТИРОВАНИЕ ФИЛЬТРАЦИИ ИНСТРУМЕНТОВ ПО ТАРИФАМ")
    print("=" * 60)

    # Инициализируем систему инструментов
    from ai_integration import handlers
    tool_discovery.discover_tools_from_module(handlers)

    tiers = ["LIGHT", "STANDARD", "PREMIUM"]
    test_results = {}

    for tier in tiers:
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
            'PREMIUM': ['set_contact_alert', 'set_activity_alert', 'research_and_plan']
        }

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

        test_results[tier] = {
            'total_tools': len(available_tools),
            'tool_names': tool_names,
            'key_tools_found': len(found_tools),
            'key_tools_total': len(tier_key_tools),
            'forbidden_found': len(found_forbidden)
        }

    return test_results

def analyze_results(results):
    """Анализ результатов тестирования"""

    print(f"\n{'='*60}")
    print("📊 АНАЛИЗ РЕЗУЛЬТАТОВ")
    print('='*60)

    # Проверяем фильтрацию
    filtering_ok = True
    for tier, results in results.items():
        if results['forbidden_found'] > 0:
            print(f"❌ {tier}: Найдены запрещенные инструменты ({results['forbidden_found']})")
            filtering_ok = False
        if results['key_tools_found'] < results['key_tools_total']:
            print(f"⚠️ {tier}: Отсутствуют ключевые инструменты ({results['key_tools_found']}/{results['key_tools_total']})")
            filtering_ok = False

    if filtering_ok:
        print("✅ ФИЛЬТРАЦИЯ ИНСТРУМЕНТОВ: Все тарифы корректно настроены")
        return True
    else:
        print("❌ ФИЛЬТРАЦИЯ ИНСТРУМЕНТОВ: Требуется доработка")
        return False

if __name__ == "__main__":
    print("🚀 ЗАПУСК УПРОЩЕННОГО ТЕСТИРОВАНИЯ ФИЛЬТРАЦИИ ИНСТРУМЕНТОВ")
    print("=" * 80)

    results = test_tool_filtering()
    success = analyze_results(results)

    print(f"\n🏆 ОБЩИЙ РЕЗУЛЬТАТ: {'✅ УСПЕХ' if success else '❌ ТРЕБУЕТСЯ ДОРАБОТКА'}")

    if not success:
        print("\n💡 РЕКОМЕНДАЦИИ:")
        print("   - Проверить списки инструментов в dynamic_tools.py")
        print("   - Убедиться, что PREMIUM инструменты не попадают в STANDARD")
        print("   - Проверить правильность списков в tools.py")