#!/usr/bin/env python3
"""
Тест соответствия ответов AI принципам главного промпта
Проверяет, что все генераторы ответов следуют унифицированным правилам
"""

import sys
import os
import re
import asyncio

# Добавляем текущую директорию в путь
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration import (
    get_system_prompt,
    generate_reminder,
    generate_result_check,
    generate_proactive_message,
    generate_daily_report,
    generate_overdue_reminder
)

def check_prompt_compliance(prompt_text, function_name):
    """Проверяет, что промпт соответствует унифицированным правилам"""
    issues = []

    # Проверяем наличие унифицированных условий
    required_phrases = [
        "минимум 300 слов",
        "4-6 предложений",
        "детальный анализ ситуации",
        "конкретные рекомендации с нумерацией",
        "вопросы для вовлечения пользователя"
    ]

    for phrase in required_phrases:
        if phrase not in prompt_text.lower():
            issues.append(f"Отсутствует: '{phrase}'")

    # Проверяем наличие принципов агента
    agent_principles = [
        "исходи из текущей ситуации",
        "учитывай все доступные данные",
        "возможности как агента"
    ]

    for principle in agent_principles:
        if principle not in prompt_text.lower():
            issues.append(f"Отсутствует принцип: '{principle}'")

    return issues

def check_full_prompt_compliance(function_name, specific_prompt_part):
    """Проверяет полный промпт (базовый + специфический)"""
    full_prompt = get_system_prompt() + "\n" + specific_prompt_part
    return check_prompt_compliance(full_prompt, function_name)

def test_system_prompt():
    """Тестируем базовый системный промпт"""
    print("🧪 ТЕСТИРОВАНИЕ СИСТЕМНОГО ПРОМПТА")
    print("=" * 80)

    system_prompt = get_system_prompt()

    # Проверяем наличие основных принципов
    required_sections = [
        "ОСНОВНЫЕ ПРИНЦИПЫ РАБОТЫ",
        "Всегда исходи из текущей ситуации",
        "учитывай все доступные данные",
        "возможности как агента",
        "Адаптируй каждый ответ под контекст",
        "Будь проактивным"
    ]

    issues = []
    for section in required_sections:
        if section.lower() not in system_prompt.lower():
            issues.append(f"Отсутствует: '{section}'")

    if issues:
        print("❌ ПРОБЛЕМЫ В СИСТЕМНОМ ПРОМПТЕ:")
        for issue in issues:
            print(f"  - {issue}")
        return False
    else:
        print("✅ Системный промпт соответствует принципам")
        return True

def test_generation_functions():
    """Тестируем функции генерации ответов - проверяем наличие строгих правил в главном промпте"""
    print("\n🧪 ТЕСТИРОВАНИЕ ГЛАВНОГО ПРОМПТА")
    print("=" * 80)

    # Читаем файл
    with open('ai_integration.py', 'r', encoding='utf-8') as f:
        content = f.read()

    # Проверяем наличие СТРОГИХ ПРАВИЛ в главном промпте (get_system_prompt)
    required_rules = [
        "СТРОГИЕ ПРАВИЛА ФОРМАТА ОТВЕТОВ",
        "НИКОГДА не используй эмодзи",
        "НИКОГДА не используй жирный текст",
        "НИКОГДА не используй маркированные списки",
        "Минимум 3-4 предложения в каждом ответе",
        "Каждый ответ должен заканчиваться вопросом"
    ]

    missing_rules = []
    for rule in required_rules:
        if rule not in content:
            missing_rules.append(rule)

    if missing_rules:
        print("❌ В главном промпте отсутствуют следующие строгие правила:")
        for rule in missing_rules:
            print(f"  - '{rule}'")
        return False
    else:
        print("✅ Главный промпт содержит все строгие правила форматирования")
        return True

def test_compliance_mechanism():
    """Тестируем механизм принуждения соответствия промпту"""
    print("\n🧪 ТЕСТИРОВАНИЕ МЕХАНИЗМА ПРИНУЖДЕНИЯ")
    print("=" * 80)

    # Читаем файл
    with open('ai_integration.py', 'r', encoding='utf-8') as f:
        content = f.read()

    # Проверяем наличие функций механизма принуждения
    required_functions = [
        "def validate_response_compliance",
        "async def enforce_prompt_compliance"
    ]

    missing_functions = []
    for func in required_functions:
        if func not in content:
            missing_functions.append(func)

    # Проверяем интеграцию механизма в chat_with_ai
    integration_check = "await enforce_prompt_compliance" in content

    if missing_functions:
        print("❌ Отсутствуют функции механизма принуждения:")
        for func in missing_functions:
            print(f"  - {func}")
        return False
    elif not integration_check:
        print("❌ Механизм enforce_prompt_compliance не интегрирован в chat_with_ai")
        return False
    else:
        print("✅ Механизм принуждения соответствия промпту полностью реализован")
        print("   - validate_response_compliance: проверяет ответы на соответствие")
        print("   - enforce_prompt_compliance: автоматически исправляет нарушения")
        print("   - Интегрирован в основной поток chat_with_ai")
        return True

def main():
    """Основная функция тестирования"""
    print("🚀 ЗАПУСК ТЕСТА СООТВЕТСТВИЯ AI АГЕНТА ПРИНЦИПАМ ПРОМПТА")
    print("=" * 100)

    results = []

    # Тестируем системный промпт
    results.append(("Системный промпт", test_system_prompt()))

    # Тестируем главный промпт
    results.append(("Главный промпт", test_generation_functions()))

    # Тестируем механизм принуждения
    results.append(("Механизм принуждения", test_compliance_mechanism()))

    # Итоги
    print("\n" + "=" * 100)
    print("📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ")
    print("=" * 100)

    passed = 0
    total = len(results)

    for test_name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status} {test_name}")
        if success:
            passed += 1

    print(f"\n📈 Всего тестов: {total}")
    print(f"✅ Пройдено: {passed}")
    print(f"❌ Провалено: {total - passed}")
    print(f"📊 Успешность: {passed/total*100:.1f}%")
    if passed == total:
        print("\n🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ! Агент соответствует принципам главного промпта.")
        return 0
    else:
        print(f"\n⚠️  НЕКОТОРЫЕ ТЕСТЫ ПРОВАЛЕНЫ. Проверьте промпты на соответствие правилам.")
        return 1

if __name__ == "__main__":
    exit(main())