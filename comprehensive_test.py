#!/usr/bin/env python3
"""
Всестороннее тестирование всех возможных запросов к AI боту
"""

import asyncio
import sys
import os
import json
import re

# Добавляем текущую директорию в путь
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration import classify_user_intent, smart_fallback_handler

def test_all_possible_queries():
    """Тестируем все возможные типы запросов к боту"""

    test_categories = {
        "Управление задачами": [
            ("Добавь задачу позвонить маме завтра в 15:00", "add_task", "прямая команда создания"),
            ("Напомни купить продукты через 2 часа", "add_task", "с относительным временем"),
            ("Создай задачу подготовить отчет к вечеру", "add_task", "с неопределенным временем"),
            ("Показать список задач", "list_tasks", "просмотр всех задач"),
            ("Мои дела", "list_tasks", "неформальный запрос списка"),
            ("Что у меня запланировано", "list_tasks", "другой вариант просмотра"),
            ("Сделал позвонить маме", "complete_task", "завершение по названию"),
            ("Выполнил задачу купить продукты", "complete_task", "завершение с ключевым словом"),
            ("Готово с отчетом", "complete_task", "короткая форма завершения"),
            ("Удали задачу позвонить маме", "delete_task", "удаление по названию"),
            ("Удали все задачи", "delete_all_tasks", "удаление всех задач"),
            ("Удали её", "delete_task", "контекстное удаление"),
            ("Измени задачу позвонить маме на позвонить папе", "edit_task", "изменение названия"),
            ("Через 3 часа", "edit_task", "контекстное изменение времени"),
            ("Установи высокий приоритет для задачи отчет", "set_priority", "установка приоритета"),
        ],

        "Делегирование задач": [
            ("@user сделай отчет до завтра", "delegate_task", "простое делегирование"),
            ("Поручи @ivan подготовить презентацию к пятнице", "delegate_task", "делегирование с дедлайном"),
            ("Передай @maria задачу проверить документы", "delegate_task", "альтернативная формулировка"),
            ("Принял задачу отчет", "accept_delegated_task", "принятие делегированной задачи"),
            ("Отклонил задачу презентация", "reject_delegated_task", "отклонение задачи"),
            ("Статус задачи отчет", "get_delegation_progress", "проверка прогресса"),
        ],

        "Поиск партнеров": [
            ("Найди людей", "find_partners", "общий поиск"),
            ("Найди людей с похожими интересами", "find_partners", "поиск по интересам"),
            ("Похожие увлечения", "find_partners", "короткий запрос"),
            ("Кто может помочь с проектом", "find_partners", "поиск помощников"),
            ("Рекомендуй контакты", "find_partners", "рекомендации"),
        ],

        "Управление профилем": [
            ("Живу в Москве", "update_profile", "обновление города"),
            ("Работаю в IT компании", "update_profile", "обновление работы"),
            ("Увлекаюсь бегом и чтением", "update_profile", "обновление интересов"),
            ("Мои навыки: Python, SQL, аналитика", "update_profile", "обновление навыков"),
            ("Мои цели: похудеть, выучить английский", "update_profile", "обновление целей"),
            ("Мое время 14:30", "update_profile", "обновление текущего времени"),
            ("Часовой пояс Europe/Moscow", "update_profile", "обновление timezone"),
        ],

        "Управление подпиской": [
            ("Статус подписки", "check_subscription_status", "проверка статуса"),
            ("Подписка активна?", "check_subscription_status", "проверка активности"),
            ("Оплати подписку", "create_subscription_payment", "оплата подписки"),
            ("Купить подписку", "create_subscription_payment", "покупка подписки"),
            ("Отменить подписку", "cancel_subscription", "отмена подписки"),
        ],

        "Контекстные и сложные запросы": [
            ("Напомни", "add_task", "неполная команда"),
            ("Через час", "edit_task", "относительное время без контекста"),
            ("Высокий приоритет", "set_priority", "установка приоритета без указания задачи"),
            ("Детали задачи отчет", "get_task_details", "запрос деталей"),
            ("Предложи альтернативы для задачи", "suggest_alternatives", "предложения альтернатив"),
        ],

        "Приветствия и общение": [
            ("Привет", "unknown", "простое приветствие"),
            ("Здравствуй", "unknown", "формальное приветствие"),
            ("Как дела?", "unknown", "неформальное общение"),
            ("Спасибо", "unknown", "выражение благодарности"),
        ]
    }

    print("🧪 ВСЕСТОРОННЕЕ ТЕСТИРОВАНИЕ ЗАПРОСОВ К AI БОТУ")
    print("=" * 80)

    total_tests = 0
    passed_tests = 0
    failed_tests = []

    for category, queries in test_categories.items():
        print(f"\n📂 {category}")
        print("-" * 50)

        for message, expected_intent, description in queries:
            total_tests += 1
            print(f"\n📝 Запрос: '{message}'")
            print(f"📋 Описание: {description}")
            print(f"🎯 Ожидаемое намерение: {expected_intent}")

            # Определяем mentions_str для делегирования
            mentions_str = 'нет'
            if '@' in message:
                mention_match = re.search(r'@(\w+)', message)
                if mention_match:
                    mentions_str = f"@{mention_match.group(1)}"

            # Классифицируем намерение
            intent = classify_user_intent(message, mentions_str)

            actual_intent = intent['type']
            confidence = intent['confidence']

            print(f"🤖 Распознанное намерение: {actual_intent} (уверенность: {confidence:.2f})")
            print(f"📊 Параметры: {intent['params']}")

            # Проверяем результат
            if actual_intent == expected_intent:
                status = "✅ PASS"
                passed_tests += 1
            else:
                status = "❌ FAIL"
                failed_tests.append({
                    'message': message,
                    'expected': expected_intent,
                    'actual': actual_intent,
                    'confidence': confidence
                })

            print(f"📊 Результат: {status}")

            # Проверяем уверенность
            if confidence >= 0.7:
                print("💪 Высокая уверенность")
            elif confidence >= 0.5:
                print("🤔 Средняя уверенность")
            else:
                print("😟 Низкая уверенность")

    print("\n" + "=" * 80)
    print("📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ")
    print("=" * 80)
    print(f"📈 Всего тестов: {total_tests}")
    print(f"✅ Пройдено: {passed_tests}")
    print(f"❌ Провалено: {len(failed_tests)}")
    print(f"📊 Успешность: {passed_tests/total_tests*100:.1f}%")
    if failed_tests:
        print("\n❌ ПРОВАЛЕННЫЕ ТЕСТЫ:")
        for fail in failed_tests:
            print(f"  • '{fail['message']}' → ожидалось: {fail['expected']}, получено: {fail['actual']} (уверенность: {fail['confidence']:.2f})")

    print("\n🎯 РЕКОМЕНДАЦИИ:")
    if len(failed_tests) == 0:
        print("  ✅ Все тесты пройдены! AI отлично понимает все типы запросов.")
    else:
        print(f"  📝 Найдено {len(failed_tests)} проблем. Рекомендуется улучшить паттерны распознавания.")

    return passed_tests == total_tests

if __name__ == "__main__":
    success = test_all_possible_queries()
    sys.exit(0 if success else 1)
