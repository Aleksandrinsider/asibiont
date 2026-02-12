#!/usr/bin/env python3
"""
Живой тест диалога агента - естественное развитие разговора
Тестирование качеств агента для LIGHT тарифа
"""
import asyncio
import sys
import os
import json
from datetime import datetime

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.autonomous_agent import chat_with_ai

class LiveDialogTester:
    """Тестер естественного диалога с агентом для LIGHT тарифа"""

    def __init__(self):
        self.tier = "LIGHT"
        self.conversation_history = []
        self.user_id = 77777  # LIGHT тариф пользователь
        self.step = 0

    async def run_natural_dialog_test(self):
        """Запуск естественного теста диалога для LIGHT тарифа"""

        print("🎭 ЕСТЕСТВЕННЫЙ ТЕСТ ДИАЛОГА - Тариф LIGHT")
        print("=" * 60)
        print("Сценарий: Обычный пользователь знакомится с ботом")
        print("Тариф: LIGHT (базовый)")
        print("Подход: Полностью естественное развитие диалога")
        print("Без заготовок - реальные пользовательские сценарии")
        print("=" * 60)

        # Реальные пользовательские сценарии для LIGHT тарифа
        user_scenarios = [
            "Привет! Расскажи что умеешь",
            "Хочу создать задачу на завтра",
            "Какие у меня задачи?",
            "Найди мне партнеров по Python",
            "Покажи мой профиль",
            "Что посоветуешь сделать сегодня?",
            "Удалить задачу",
            "Создать напоминание на вечер",
            "Как работает подписка?",
            "Спасибо за помощь!"
        ]

        print("\n📋 СЦЕНАРИИ ТЕСТИРОВАНИЯ:")
        for i, scenario in enumerate(user_scenarios, 1):
            print(f"  {i}. {scenario}")

        print(f"\n👤 ПОЛЬЗОВАТЕЛЬ: ID {self.user_id} (LIGHT тариф)")
        print("🤖 АГЕНТ: ASI Biont с естественным поведением")

        # Естественный диалог
        for step, user_message in enumerate(user_scenarios, 1):
            self.step = step
            print(f"\n==================================================")
            print(f"ШАГ {self.step}/10 - {datetime.now().strftime('%H:%M:%S')}")
            print("=" * 50)

            try:
                print(f"👤 ПОЛЬЗОВАТЕЛЬ: {user_message}")

                # Получаем ответ агента
                response = await chat_with_ai(user_message, user_id=self.user_id)

                agent_response = response.get('response', 'Ошибка ответа')
                tools_called = response.get('tool_calls', [])
                tools_used = response.get('tools_used', [])

                print(f"🤖 АГЕНТ: {agent_response}")
                print(f"🔧 ИНСТРУМЕНТЫ: {'Вызваны' if tools_called else 'Не вызваны'}")

                if tools_used:
                    print(f"   Список: {', '.join(tools_used)}")

                # Сохраняем в историю
                self.conversation_history.append({
                    'step': self.step,
                    'user': user_message,
                    'agent': agent_response,
                    'tools': tools_used,
                    'timestamp': datetime.now().isoformat()
                })

            except Exception as e:
                print(f"❌ ОШИБКА: {e}")
                break

            # Пауза между шагами для естественности
            await asyncio.sleep(2)

        # Анализ результатов
        print(f"\n{'='*50}")
        print("📊 АНАЛИЗ РЕЗУЛЬТАТОВ")
        print('='*50)

        total_tools_called = sum(len(msg['tools']) for msg in self.conversation_history)
        steps_with_tools = sum(1 for msg in self.conversation_history if msg['tools'])

        print(f"📈 СТАТИСТИКА:")
        print(f"   Всего шагов: {len(self.conversation_history)}")
        print(f"   Вызовов инструментов: {total_tools_called}")
        print(f"   Шагов с инструментами: {steps_with_tools}")
        print(f"   Среднее инструментов на шаг: {total_tools_called/len(self.conversation_history):.1f}" if self.conversation_history else "   Среднее инструментов на шаг: 0.0")

        # Анализ качества для LIGHT тарифа
        print(f"\n🎯 ОЦЕНКА КАЧЕСТВА ДЛЯ LIGHT ТАРИФА:")

        # Естественность - отсутствие форматирования
        natural_responses = sum(1 for msg in self.conversation_history
                               if '**' not in msg['agent'] and '1.' not in msg['agent'] and '- ' not in msg['agent'])
        print(f"   💬 ЕСТЕСТВЕННОСТЬ: {natural_responses}/{len(self.conversation_history)} ответов без форматирования")

        # Функциональность - правильные вызовы инструментов
        functional_steps = 0
        for msg in self.conversation_history:
            user_msg = msg['user'].lower()
            tools = msg['tools']

            # Проверяем соответствие инструментов запросам
            if 'задач' in user_msg and 'list_tasks' in tools:
                functional_steps += 1
            elif 'создать' in user_msg and 'add_task' in tools:
                functional_steps += 1
            elif 'партнер' in user_msg and 'find_partners' in tools:
                functional_steps += 1
            elif 'профиль' in user_msg and 'show_profile' in tools:
                functional_steps += 1
            elif 'удалить' in user_msg and 'delete_task' in tools:
                functional_steps += 1

        print(f"   ⚡ ФУНКЦИОНАЛЬНОСТЬ: {functional_steps}/{len(self.conversation_history)} правильных вызовов инструментов")

        # Проактивность - предложения действий
        proactive_responses = sum(1 for msg in self.conversation_history
                                 if any(word in msg['agent'].lower()
                                       for word in ['предлагаю', 'создадим', 'найдем', 'давай', 'можешь', 'попробуй']))
        print(f"   🚀 ПРОАКТИВНОСТЬ: {proactive_responses}/{len(self.conversation_history)} проактивных ответов")

        # Контекстность - упоминания профиля, времени, задач
        contextual_responses = sum(1 for msg in self.conversation_history
                                  if any(word in msg['agent'].lower()
                                        for word in ['профиль', 'задач', 'сегодня', 'завтра', 'вечер']))
        print(f"   🎯 КОНТЕКСТНОСТЬ: {contextual_responses}/{len(self.conversation_history)} контекстных ответов")

        # Итоговая оценка
        total_score = natural_responses + functional_steps + proactive_responses + contextual_responses
        max_score = len(self.conversation_history) * 4
        print(f"\n🏆 ИТОГОВАЯ ОЦЕНКА: {total_score}/{max_score} (макс. {max_score})")

        # Рекомендации по улучшениям
        print(f"\n💡 РЕКОМЕНДАЦИИ ПО УЛУЧШЕНИЯМ:")

        if natural_responses < len(self.conversation_history):
            print("   - Улучшить естественность: убрать форматирование списков")
        if functional_steps < len(self.conversation_history) * 0.7:
            print("   - Исправить функциональность: правильные вызовы инструментов")
        if proactive_responses < len(self.conversation_history) * 0.5:
            print("   - Добавить проактивности: больше предложений действий")
        if contextual_responses < len(self.conversation_history) * 0.6:
            print("   - Улучшить контекстность: учитывать профиль и время")

        return self.conversation_history

async def main():
    """Запуск естественного теста для LIGHT тарифа"""

    print("🚀 НАЧИНАЕМ ЕСТЕСТВЕННЫЙ ТЕСТ ДЛЯ LIGHT ТАРИФА")
    print("=" * 60)

    tester = LiveDialogTester()
    results = await tester.run_natural_dialog_test()

    # Сохраняем результаты
    with open('natural_dialog_test_results.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n💾 РЕЗУЛЬТАТЫ СОХРАНЕНЫ В: natural_dialog_test_results.json")

if __name__ == "__main__":
    asyncio.run(main())