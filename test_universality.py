#!/usr/bin/env python3
"""
Тест универсальности ASI Biont агента
Проверяем работу с повседневными задачами, знакомствами и не-бизнес сценариями
"""

import asyncio
import json
import sys
import os

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.autonomous_agent import HybridAutonomousAgent
from models import SubscriptionTier

async def test_scenario(name, messages, tier=SubscriptionTier.LIGHT):
    """Тестируем сценарий с несколькими сообщениями"""
    print(f"\n{'='*60}")
    print(f"TEST SCENARIO: {name}")
    print(f"TIER: {tier.value}")
    print(f"{'='*60}")

    agent = HybridAutonomousAgent()
    conversation_history = []

    for i, user_message in enumerate(messages, 1):
        print(f"\nUSER {i}: {user_message}")

        try:
            # Имитируем контекст пользователя
            user_context = {
                'id': 99999,
                'username': 'test_user',
                'subscription_tier': tier,
                'city': 'Москва',
                'timezone': 'Europe/Moscow'
            }

            # Используем тестовый user_id = 1 (предполагаем, что он существует)
            response = await agent.process_request(user_message, 1)
            print(f"AGENT {i}: {response[:200]}{'...' if len(response) > 200 else ''}")

            conversation_history.append({
                'user': user_message,
                'agent': response
            })

        except Exception as e:
            print(f"❌ ОШИБКА: {e}")
            conversation_history.append({
                'user': user_message,
                'error': str(e)
            })

    return conversation_history

async def run_universality_tests():
    """Запускаем тесты универсальности"""

    scenarios = [
        {
            'name': 'ПОВСЕДНЕВНЫЕ ЗАДАЧИ',
            'messages': [
                'Привет! Помоги мне спланировать выходные',
                'Я люблю готовить, но не знаю что приготовить на ужин',
                'У меня болит голова, что делать?',
                'Как лучше организовать домашнюю библиотеку?'
            ]
        },
        {
            'name': 'ЗНАКОМСТВА И СОЦИАЛЬНЫЕ СВЯЗИ',
            'messages': [
                'Привет! Я ищу новых друзей в Москве',
                'Мне нравится бегать по утрам, хочу найти партнера для пробежек',
                'Интересуюсь фотографией, хочу познакомиться с единомышленниками',
                'Ищу кого-то для совместных путешествий'
            ]
        },
        {
            'name': 'РАЗВЛЕЧЕНИЯ И ДОСУГ',
            'messages': [
                'Что посмотреть вечером?',
                'Посоветуй хорошую книгу для чтения',
                'Какие фильмы сейчас в тренде?',
                'Где можно интересно провести выходные в Москве?'
            ]
        },
        {
            'name': 'ЛИЧНЫЕ ФИНАНСЫ И ПОКУПКИ',
            'messages': [
                'Хочу купить новый телефон, что посоветуешь?',
                'Как сэкономить на коммунальных платежах?',
                'Планирую отпуск, сколько примерно нужно денег?',
                'Нужно выбрать подарок для друга'
            ]
        },
        {
            'name': 'ЗДОРОВЬЕ И СПОРТ',
            'messages': [
                'Хочу начать заниматься спортом, с чего начать?',
                'У меня проблемы со сном, что делать?',
                'Как правильно питаться?',
                'Нужна программа тренировок для дома'
            ]
        },
        {
            'name': 'ОБРАЗОВАНИЕ И САМОРАЗВИТИЕ',
            'messages': [
                'Хочу выучить английский, как лучше?',
                'Интересуюсь программированием, с чего начать?',
                'Как развить навык публичных выступлений?',
                'Нужно подготовиться к собеседованию'
            ]
        }
    ]

    all_results = {}

    for scenario in scenarios:
        # Тестируем на LIGHT тарифе
        light_results = await test_scenario(
            f"{scenario['name']} (LIGHT)",
            scenario['messages'],
            SubscriptionTier.LIGHT
        )

        # Тестируем на STANDARD тарифе
        standard_results = await test_scenario(
            f"{scenario['name']} (STANDARD)",
            scenario['messages'],
            SubscriptionTier.STANDARD
        )

        all_results[scenario['name']] = {
            'light': light_results,
            'standard': standard_results
        }

    # Сохраняем результаты
    with open('universality_test_results.json', 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print("ANALYSIS OF TEST RESULTS")
    print(f"{'='*60}")

    # Анализируем результаты
    analyze_results(all_results)

def analyze_results(results):
    """Анализируем результаты тестирования"""

    categories = {
        'personal_tasks': 'ПОВСЕДНЕВНЫЕ ЗАДАЧИ',
        'social': 'ЗНАКОМСТВА И СОЦИАЛЬНЫЕ СВЯЗИ',
        'entertainment': 'РАЗВЛЕЧЕНИЯ И ДОСУГ',
        'finance': 'ЛИЧНЫЕ ФИНАНСЫ И ПОКУПКИ',
        'health': 'ЗДОРОВЬЕ И СПОРТ',
        'education': 'ОБРАЗОВАНИЕ И САМОРАЗВИТИЕ'
    }

    print("\nCONCLUSIONS ON AGENT UNIVERSALITY:")
    print("=" * 50)

    for category_key, category_name in categories.items():
        if category_name in results:
            data = results[category_name]

            print(f"\nCATEGORY {category_name}:")

            # Анализ LIGHT тарифа
            light_responses = [msg for msg in data['light'] if 'agent' in msg]
            light_errors = [msg for msg in data['light'] if 'error' in msg]

            # Анализ STANDARD тарифа
            standard_responses = [msg for msg in data['standard'] if 'agent' in msg]
            standard_errors = [msg for msg in data['standard'] if 'error' in msg]

            print(f"  LIGHT: {len(light_responses)}/{len(data['light'])} successful responses")
            if light_errors:
                print(f"     Errors: {len(light_errors)}")

            print(f"  STANDARD: {len(standard_responses)}/{len(data['standard'])} successful responses")
            if standard_errors:
                print(f"     Errors: {len(standard_errors)}")

            # Проверяем использование инструментов
            light_tools_used = any('🔍' in msg.get('agent', '') or '🎯' in msg.get('agent', '') or '👥' in msg.get('agent', '') for msg in data['light'])
            standard_tools_used = any('🔍' in msg.get('agent', '') or '🎯' in msg.get('agent', '') or '👥' in msg.get('agent', '') for msg in data['standard'])

            print(f"  Tools: LIGHT={light_tools_used}, STANDARD={standard_tools_used}")

    print(f"\n{'='*60}")
    print("GENERAL RECOMMENDATIONS:")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(run_universality_tests())