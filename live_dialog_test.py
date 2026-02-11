#!/usr/bin/env python3
"""
Живой тест диалога агента - 20 шагов с реальным пользователем
Тестирование качеств агента высокого уровня
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
    """Тестер живого диалога с агентом"""

    def __init__(self):
        self.conversation_history = []
        self.user_id = 77777  # Используем существующего пользователя LIGHT
        self.step = 0

    async def generate_user_message(self, context=""):
        """Генерирует естественное сообщение пользователя через DeepSeek"""
        try:
            import aiohttp
            from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL

            system_prompt = f"""Ты - обычный пользователь телеграм-бота для управления задачами.
Пиши естественные, разговорные сообщения как реальный человек.
Не используй шаблоны или заготовки.

КОНТЕКСТ ДИАЛОГА:
{context}

ПРАВИЛА:
- Пиши короткие, естественные сообщения (1-3 предложения)
- Используй разговорный стиль: "привет", "спасибо", "ок", "ясно"
- Задавай вопросы, когда интересно
- Проси помощи, когда нужно
- Реагируй на предложения агента
- Не повторяйся, будь разнообразным

ПРИМЕРЫ СООБЩЕНИЙ:
"Привет, что умеешь?"
"Создай задачу сходить в магазин завтра в 10"
"Что у меня по задачам?"
"Найди партнеров для бизнеса"
"Спасибо, пока"

Сгенерируй ОДНО сообщение пользователя для продолжения диалога:"""

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "Сгенерируй следующее сообщение пользователя"}
            ]

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": DEEPSEEK_MODEL,
                        "messages": messages,
                        "temperature": 0.8,
                        "max_tokens": 100
                    }
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        message = result['choices'][0]['message']['content'].strip()
                        # Очищаем от кавычек если есть
                        message = message.strip('"').strip("'")
                        return message
                    else:
                        return "Привет"

        except Exception as e:
            print(f"Error generating user message: {e}")
            return "Привет"

    async def run_dialog_test(self):
        """Запуск теста диалога из 20 шагов"""

        print("🎭 ЖИВОЙ ТЕСТ ДИАЛОГА АГЕНТА")
        print("=" * 50)
        print("Цель: Проверить качества агента высокого уровня")
        print("Шаги: 20 сообщений в реальном времени")
        print("Пользователь: Генерируется DeepSeek (натуральные сообщения)")
        print("=" * 50)

        # Качества для проверки
        qualities = {
            "🎯 КОНТЕКСТНОСТЬ": "Учитывает время, погоду, профиль, историю",
            "🚀 ПРОАКТИВНОСТЬ": "Предлагает полезные действия, но не навязчиво",
            "⚡ ИСПОЛНЕНИЕ": "Действительно вызывает инструменты и выполняет",
            "💬 ЕСТЕСТВЕННОСТЬ": "Разговорный стиль, эмпатия",
            "🔄 АДАПТИВНОСТЬ": "Меняет поведение по ситуации",
            "🎯 ТОЧНОСТЬ": "Правильно понимает, не выдумывает",
            "💡 ЭФФЕКТИВНОСТЬ": "Конкретные, actionable предложения"
        }

        print("\n📋 ПРОВЕРЯЕМЫЕ КАЧЕСТВА:")
        for quality, desc in qualities.items():
            print(f"  {quality}: {desc}")

        print(f"\n👤 ПОЛЬЗОВАТЕЛЬ: ID {self.user_id} (LIGHT тариф)")
        print("🤖 АГЕНТ: ASI Biont с умным поведением")
        # Начало диалога
        context = "Начало разговора. Пользователь только что написал первое сообщение."

        for step in range(1, 21):
            print(f"\n{'='*50}")
            print(f"ШАГ {step}/20 - {datetime.now().strftime('%H:%M:%S')}")
            print('='*50)

            # Генерируем сообщение пользователя
            user_message = await self.generate_user_message(context)
            print(f"👤 ПОЛЬЗОВАТЕЛЬ: {user_message}")

            # Получаем ответ агента
            try:
                agent_response = await chat_with_ai(user_message, user_id=self.user_id)
                response_text = agent_response['response']
                tool_calls = agent_response.get('tool_calls', [])
                tools_used = agent_response.get('tools_used', [])

                print(f"🤖 АГЕНТ: {response_text[:200]}{'...' if len(response_text) > 200 else ''}")

                if tool_calls:
                    print(f"🔧 ВЫЗВАННЫЕ ИНСТРУМЕНТЫ: {len(tool_calls)}")
                    for i, tc in enumerate(tool_calls[:3]):  # Показываем первые 3
                        tool_name = tc.get('function', {}).get('name', 'unknown')
                        print(f"   {i+1}. {tool_name}")
                    if len(tool_calls) > 3:
                        print(f"   ... и еще {len(tool_calls)-3}")
                else:
                    print("🔧 ИНСТРУМЕНТЫ: Не вызваны")

                # Сохраняем в историю
                self.conversation_history.append({
                    'step': step,
                    'user': user_message,
                    'agent': response_text,
                    'tools': [tc.get('function', {}).get('name') for tc in tool_calls],
                    'timestamp': datetime.now().isoformat()
                })

                # Обновляем контекст для следующего сообщения
                context = f"Последние сообщения:\n"
                for msg in self.conversation_history[-3:]:  # Последние 3
                    context += f"Пользователь: {msg['user'][:100]}...\n"
                    context += f"Агент: {msg['agent'][:100]}...\n"
                    if msg['tools']:
                        context += f"Инструменты: {', '.join(msg['tools'])}\n"
                    context += "\n"

            except Exception as e:
                print(f"❌ ОШИБКА: {e}")
                break

            # Небольшая пауза между шагами
            await asyncio.sleep(1)

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
        print(f"   Среднее инструментов на шаг: {total_tools_called/len(self.conversation_history):.1f}")
        # Анализ по качествам
        print(f"\n🎯 ОЦЕНКА КАЧЕСТВ:")

        # Контекстность - проверяем упоминания времени/погоды/профиля
        context_mentions = sum(1 for msg in self.conversation_history
                              if any(word in msg['agent'].lower()
                                    for word in ['ночь', 'день', 'утро', 'вечер', 'погода', 'профиль', 'интерес']))
        print(f"   🎯 КОНТЕКСТНОСТЬ: {context_mentions}/{len(self.conversation_history)} упоминаний контекста")

        # Исполнение - проверяем реальные вызовы инструментов
        execution_score = steps_with_tools
        print(f"   ⚡ ИСПОЛНЕНИЕ: {execution_score}/{len(self.conversation_history)} шагов с реальными действиями")

        # Естественность - проверяем отсутствие формальных фраз
        formal_phrases = ['могу помочь', 'предлагаю', 'давай', 'посмотрим']
        unnatural_count = sum(1 for msg in self.conversation_history
                             if any(phrase in msg['agent'].lower() for phrase in formal_phrases))
        natural_score = len(self.conversation_history) - unnatural_count
        print(f"   💬 ЕСТЕСТВЕННОСТЬ: {natural_score}/{len(self.conversation_history)} естественных ответов")

        # Эффективность - проверяем конкретные предложения
        concrete_suggestions = sum(1 for msg in self.conversation_history
                                 if any(word in msg['agent'].lower()
                                       for word in ['создай', 'найди', 'посмотри', 'рекомендую', 'предлагаю']))
        print(f"   💡 ЭФФЕКТИВНОСТЬ: {concrete_suggestions}/{len(self.conversation_history)} конкретных предложений")

        print(f"\n💾 ДИАЛОГ СОХРАНЕН В: conversation_log.json")

        # Сохраняем лог
        with open('conversation_log.json', 'w', encoding='utf-8') as f:
            json.dump(self.conversation_history, f, ensure_ascii=False, indent=2)

async def main():
    tester = LiveDialogTester()
    await tester.run_dialog_test()

if __name__ == "__main__":
    asyncio.run(main())