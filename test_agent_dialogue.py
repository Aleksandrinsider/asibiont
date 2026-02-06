#!/usr/bin/env python3
"""
Тест диалога с AI агентом - симуляция реального взаимодействия
Проверяет соответствие требованиям из my.txt
"""

import asyncio
import os
import sys
import json
from datetime import datetime, timezone

# Установка локального режима
os.environ['LOCAL'] = '1'

# Добавление пути к проекту
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.autonomous_agent import chat_with_ai
from config import TIMEZONE

def simulate_dialogue():
    """Симуляция диалога с агентом"""

    # Тестовые сообщения пользователя
    test_messages = [
        "Привет! Расскажи о своих возможностях",
        "Создай задачу: подготовить презентацию к завтра",
        "Найди единомышленников по Python",
        "Какие у меня задачи?",
        "Задача выполнена? Если да - какой результат?",
    ]

    print("🧪 ТЕСТИРОВАНИЕ ДИАЛОГА С AI АГЕНТОМ")
    print("=" * 50)

    # Имитация контекста пользователя
    user_context = {
        'username': 'test_user',
        'tier': 'STANDARD',
        'memory': 'Любит Python, интересуется ML, работает над стартапом',
        'current_time': datetime.now(TIMEZONE),
        'weather': 'Солнечно, +20°C',
        'news': 'Технологии: новый фреймворк для ML вышел'
    }

    results = []

    for i, user_msg in enumerate(test_messages, 1):
        print(f"\n🔹 ТЕСТ {i}: {user_msg}")
        print("-" * 40)

        try:
            # Получение ответа агента
            response = asyncio.run(chat_with_ai(
                message=user_msg,
                context=user_context,
                user_id=12345,  # Фиктивный user_id для теста
                message_type='text'
            ))

            print(f"🤖 АГЕНТ: {response.get('response', 'Нет ответа')[:200]}...")

            # Проверка соответствия требованиям
            violations = check_requirements(response.get('response', ''), user_msg)

            if violations:
                print(f"❌ НАРУШЕНИЯ: {len(violations)}")
                for v in violations[:3]:  # Показать первые 3
                    print(f"   - {v}")
            else:
                print("✅ Соответствует требованиям")

            results.append({
                'turn': i,
                'user': user_msg,
                'agent': response.get('response', ''),
                'violations': violations
            })

        except Exception as e:
            print(f"❌ ОШИБКА: {str(e)}")
            results.append({
                'turn': i,
                'user': user_msg,
                'agent': f"ОШИБКА: {str(e)}",
                'violations': [f"Exception: {str(e)}"]
            })

    # Сохранение результатов
    with open('dialogue_test_results.json', 'w', encoding='utf-8') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'total_tests': len(test_messages),
            'passed': len([r for r in results if not r['violations']]),
            'results': results
        }, f, ensure_ascii=False, indent=2)

    # Итоги
    passed = len([r for r in results if not r['violations']])
    total = len(test_messages)

    print(f"\n📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ:")
    print(f"✅ Прошло: {passed}/{total} ({passed/total*100:.1f}%)")

    if passed == total:
        print("🎉 АГЕНТ ПОЛНОСТЬЮ СООТВЕТСТВУЕТ ТРЕБОВАНИЯМ!")
    else:
        print("⚠️  Есть нарушения требований")

    return passed == total

def check_requirements(response, user_msg):
    """Проверка соответствия ключевым требованиям из my.txt"""

    violations = []

    # 1. Не использовать нумерацию, списки, жирный шрифт
    if any(char in response for char in ['1.', '2.', '3.', '-', '*', '**']):
        violations.append("Использует нумерацию или списки")

    # 2. Лаконичность и по существу
    if len(response) > 1000:
        violations.append("Ответ слишком длинный (>1000 символов)")

    # 3. Один эмодзи на ответ (правильный подсчет эмодзи)
    import re
    emoji_pattern = re.compile(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U00002500-\U00002BEF\U00002702-\U000027B0\U000024C2-\U0001F251\U0001f926-\U0001f937\U00010000-\U0010ffff\U000024C2-\U0001F251\U0001f918-\U0001f991\U0001F980-\U0001F9E0]')
    emoji_count = len(emoji_pattern.findall(response))
    if emoji_count > 2:
        violations.append(f"Слишком много эмодзи ({emoji_count})")

    # 4. Завершать конкретным предложением действия или вопросом
    last_sentences = response.split('.')[-1].strip()
    if not (last_sentences.endswith('?') or any(word in last_sentences.lower() for word in ['можешь', 'давай', 'предлагаю', 'создам', 'найду'])):
        violations.append("Не заканчивается действием или вопросом")

    # 5. Учитывать контекст и персонализацию
    if 'пользователь' in response.lower() or 'ты' in response.lower():
        if not any(word in response.lower() for word in ['python', 'ml', 'стартап', 'презентация']):
            violations.append("Недостаточная персонализация")

    # 6. Не придумывать данные
    if any(word in response.lower() for word in ['город:', 'дата рождения:', 'компания:', 'должность:']):
        violations.append("Придумывает личные данные")

    return violations

if __name__ == "__main__":
    success = simulate_dialogue()
    sys.exit(0 if success else 1)
