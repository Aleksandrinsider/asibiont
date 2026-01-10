#!/usr/bin/env python3
"""
Тестирование полных ответов AI агента на все возможные запросы
"""

import asyncio
import sys
import os
import json
import re

# Добавляем текущую директорию в путь
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration import classify_user_intent, smart_fallback_handler, enrich_response_with_engagement

def test_full_agent_responses():
    """Тестируем полные ответы агента на все типы запросов"""

    test_categories = {
        "Управление задачами": [
            ("Добавь задачу позвонить маме завтра в 15:00", "add_task"),
            ("Напомни купить продукты через 2 часа", "add_task"),
            ("Показать список задач", "list_tasks"),
            ("Мои дела", "list_tasks"),
            ("Сделал позвонить маме", "complete_task"),
            ("Удали задачу позвонить маме", "delete_task"),
            ("Удали все задачи", "delete_all_tasks"),
        ],

        "Делегирование задач": [
            ("@user сделай отчет до завтра", "delegate_task"),
            ("Поручи @ivan подготовить презентацию к пятнице", "delegate_task"),
            ("Принял задачу отчет", "accept_delegated_task"),
            ("Статус задачи отчет", "get_delegation_progress"),
        ],

        "Поиск партнеров": [
            ("Найди людей", "find_partners"),
            ("Найди людей с похожими интересами", "find_partners"),
        ],

        "Управление профилем": [
            ("Живу в Москве", "update_profile"),
            ("Увлекаюсь бегом и чтением", "update_profile"),
        ],

        "Управление подпиской": [
            ("Статус подписки", "check_subscription_status"),
            ("Оплати подписку", "create_subscription_payment"),
        ],

        "Приветствия и общение": [
            ("Привет", "greeting"),
            ("Как дела?", "unknown"),
        ]
    }

    print("🧪 ТЕСТИРОВАНИЕ ПОЛНЫХ ОТВЕТОВ AI АГЕНТА")
    print("=" * 100)

    user_id = "123456789"  # Тестовый user_id

    for category, queries in test_categories.items():
        print(f"\n📂 {category}")
        print("-" * 80)

        for message, expected_intent in queries:
            print(f"\n📝 ЗАПРОС: '{message}'")
            print(f"🎯 Ожидаемое намерение: {expected_intent}")
            print("-" * 60)

            # Определяем mentions_str для делегирования
            mentions_str = 'нет'
            if '@' in message:
                mention_match = re.search(r'@(\w+)', message)
                if mention_match:
                    mentions_str = f"@{mention_match.group(1)}"

            # 1. Классифицируем намерение
            intent = classify_user_intent(message, mentions_str)
            print(f"🤖 Распознанное намерение: {intent['type']} (уверенность: {intent['confidence']:.2f})")

            # 2. Получаем ответ через smart_fallback_handler
            try:
                ai_response_content = "Короткий ответ AI"  # Имитируем ответ AI
                fallback_result = smart_fallback_handler(
                    message=message,
                    mentions_str=mentions_str,
                    user_id=user_id,
                    ai_response_content=ai_response_content
                )

                # 3. Преобразуем результат fallback в естественный текст (как в chat_with_ai)
                if fallback_result:
                    natural_responses = []
                    for action in fallback_result:
                        result_text = action["result"]
                        func_name = action["function"]

                        if "Добавлена задача" in result_text:
                            match = re.search(
                                r"Добавлена задача '([^']+)' \(ID: \d+\) с напоминанием на ([^)]+)", result_text
                            )
                            if match:
                                title = match.group(1)
                                time_str = match.group(2)
                                natural = f'Отлично, добавил задачу "{title}" с напоминанием на {time_str}.'
                                natural_responses.append(natural)
                            else:
                                natural_responses.append(result_text)

                        elif "Завершена задача" in result_text:
                            match = re.search(r"Завершена задача '([^']+)'", result_text)
                            if match:
                                title = match.group(1)
                                natural = f'Отлично, отметил задачу "{title}" как выполненную! 👍'
                                natural_responses.append(natural)
                            else:
                                natural_responses.append(result_text)

                        elif "Задачи:" in result_text:
                            natural_responses.append(result_text)

                        elif "Удалены все задачи" in result_text:
                            natural = (
                                "Удалил все твои задачи. Теперь список пуст — можно начинать с чистого листа!"
                            )
                            natural_responses.append(natural)

                        elif "Задача" in result_text and "делегирована" in result_text:
                            natural = "Отлично, задача делегирована! Я уведомлю получателя."
                            natural_responses.append(natural)

                        else:
                            natural_responses.append(result_text)

                    final_content = "\n".join(natural_responses)
                else:
                    final_content = "Короткий ответ AI"

                # 4. Обогащаем ответ вовлечением
                final_response = enrich_response_with_engagement(
                    content=final_content,
                    user_id=user_id,
                    original_message=message
                )

                print(f"💬 ФИНАЛЬНЫЙ ОТВЕТ АГЕНТА:")
                print(f"   {final_response}")
                print("✅ УСПЕХ")

            except Exception as e:
                print(f"❌ ОШИБКА: {str(e)}")
                print("   Подробности ошибки:")
                import traceback
                traceback.print_exc()

            print()

if __name__ == "__main__":
    test_full_agent_responses()