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
    """Тестируем функции генерации ответов - проверяем наличие условий в файле"""
    print("\n🧪 ТЕСТИРОВАНИЕ ФУНКЦИЙ ГЕНЕРАЦИИ ОТВЕТОВ")
    print("=" * 80)

    # Читаем файл
    with open('ai_integration.py', 'r', encoding='utf-8') as f:
        content = f.read()

    # Проверяем наличие унифицированных условий в файле
    required_phrases = [
        "ПРАВИЛА ДЛЯ ОТВЕТА: Минимум 300 слов",
        "4-6 предложений",
        "детальный анализ ситуации",
        "конкретные рекомендации с нумерацией",
        "вопросы для вовлечения пользователя"
    ]

    missing_phrases = []
    for phrase in required_phrases:
        if phrase not in content:
            missing_phrases.append(phrase)

    if missing_phrases:
        print("❌ В файле отсутствуют следующие унифицированные условия:")
        for phrase in missing_phrases:
            print(f"  - '{phrase}'")
        return False
    else:
        print("✅ Все унифицированные условия присутствуют в файле")
        return True

def test_list_tasks_analysis():
    """Тестируем анализ для list_tasks - проверяем наличие условий в коде"""
    print("\n🧪 ТЕСТИРОВАНИЕ АНАЛИЗА LIST_TASKS")
    print("=" * 80)

    # Читаем файл
    with open('ai_integration.py', 'r', encoding='utf-8') as f:
        content = f.read()

    # Ищем оба места с analysis_system
    analysis_matches = re.findall(r'analysis_system = f"""(.*?)"""', content, re.DOTALL)

    if not analysis_matches:
        print("❌ Не найден analysis_system для list_tasks")
        return False

    all_passed = True
    for i, analysis_prompt in enumerate(analysis_matches, 1):
        print(f"\n📝 Тестируем analysis_system #{i}")
        print("-" * 60)

        # Проверяем наличие унифицированных условий
        required_phrases = [
            "ПРАВИЛА ДЛЯ ОТВЕТА: Минимум 300 слов",
            "4-6 предложений",
            "детальный анализ ситуации",
            "конкретные рекомендации с нумерацией",
            "вопросы для вовлечения пользователя"
        ]

        issues = []
        for phrase in required_phrases:
            if phrase not in analysis_prompt:
                issues.append(f"Отсутствует: '{phrase}'")

        if issues:
            print(f"❌ ПРОБЛЕМЫ В analysis_system #{i}:")
            for issue in issues:
                print(f"  - {issue}")
            all_passed = False
        else:
            print(f"✅ analysis_system #{i} содержит унифицированные условия")

    return all_passed

def main():
    """Основная функция тестирования"""
    print("🚀 ЗАПУСК ТЕСТА СООТВЕТСТВИЯ AI АГЕНТА ПРИНЦИПАМ ПРОМПТА")
    print("=" * 100)

    results = []

    # Тестируем системный промпт
    results.append(("Системный промпт", test_system_prompt()))

    # Тестируем функции генерации
    results.append(("Функции генерации", test_generation_functions()))

    # Тестируем анализ list_tasks
    results.append(("Анализ list_tasks", test_list_tasks_analysis()))

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