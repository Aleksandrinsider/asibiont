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
    """Тестер живого диалога с агентом для разных тарифов"""

    def __init__(self, tier="LIGHT"):
        self.tier = tier
        self.conversation_history = []
        # Разные пользователи для разных тарифов
        self.user_ids = {
            "LIGHT": 77777,
            "STANDARD": 88888,
            "PREMIUM": 99999
        }
        self.user_id = self.user_ids.get(tier, 77777)
        self.step = 0

    async def generate_user_message(self, context="", tier="LIGHT"):
        """Генерирует естественное сообщение пользователя через DeepSeek"""
        try:
            import aiohttp
            from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL

            tier_descriptions = {
                "LIGHT": "базовый тариф с ограниченными функциями",
                "STANDARD": "бизнес-тариф с маркетингом и делегированием",
                "PREMIUM": "премиум-тариф со всеми функциями"
            }

            system_prompt = f"""Ты - разработчик AI агента, который хочет продвинуть свой продукт на рынке и привлечь больше пользователей.

ТВОЙ ПРОФИЛЬ:
- Разработчик AI систем и автономных агентов
- Интересуешься: AI, программирование, стартапы, бизнес
- Город: Москва
- Работа: разработка AI решений
- Текущий тариф: {tier} ({tier_descriptions[tier]})

ТВОИ ЦЕЛИ:
- Продвинуть AI агента на рынке
- Привлечь больше пользователей
- Улучшить продукт
- Найти партнеров для развития
- Создать контент для продвижения

КОНТЕКСТ ДИАЛОГА:
{context}

СТИЛЬ ОБЩЕНИЯ:
- Профессиональный, но дружелюбный
- Фокус на бизнес и развитие продукта
- Интересуешься маркетингом, партнерами, аналитикой
- Задаешь конкретные вопросы
- Просишь помощи в реальных задачах продвижения

ПРИМЕРЫ СООБЩЕНИЙ ДЛЯ {tier.upper()}:
LIGHT: "Привет! Хочу найти партнеров для продвижения моего AI агента"
STANDARD: "Нужно создать пост в Telegram о преимуществах моего AI решения"
PREMIUM: "Проанализируй рынок AI агентов и найди возможности для роста"

Сгенерируй ОДНО реалистичное сообщение пользователя для продолжения диалога:"""

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
        """Запуск теста диалога из 15 шагов для конкретного тарифа"""

        print(f"🎭 ТЕСТ РАЗРАБОТЧИКА AI АГЕНТА - Тариф {self.tier.upper()}")
        print("=" * 60)
        print("Сценарий: Разработчик хочет продвинуть AI агента на рынке")
        print(f"Тариф: {self.tier.upper()}")
        print("Шаги: 15 сообщений в реальном времени")
        print("Пользователь: AI разработчик (генерируется DeepSeek)")
        print("=" * 60)

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

        print(f"\n👤 ПОЛЬЗОВАТЕЛЬ: ID {self.user_id} ({self.tier.upper()} тариф)")
        print("🤖 АГЕНТ: ASI Biont с умным поведением")

        # Основной цикл диалога (15 шагов)
        for step in range(15):
            self.step = step + 1
            print(f"\n==================================================")
            print(f"ШАГ {self.step}/15 - {datetime.now().strftime('%H:%M:%S')}")
            print("=" * 50)

            try:
                # Генерируем сообщение пользователя
                context = ""
                if self.conversation_history:
                    # Передаем последние 3 сообщения для контекста
                    recent_msgs = self.conversation_history[-3:]
                    context = "\n".join([
                        f"Пользователь: {msg['user']}\nАгент: {msg['agent'][:200]}..."
                        for msg in recent_msgs
                    ])

                user_message = await self.generate_user_message(context, self.tier)
                print(f"👤 РАЗРАБОТЧИК: {user_message}")

                # Получаем ответ агента
                from ai_integration.autonomous_agent import chat_with_ai
                response = await chat_with_ai(user_message, user_id=self.user_id)

                agent_response = response.get('response', 'Ошибка ответа')
                tools_called = response.get('tool_calls', [])

                print(f"🤖 АГЕНТ: {agent_response[:300]}{'...' if len(agent_response) > 300 else ''}")
                print(f"🔧 ИНСТРУМЕНТЫ: {'Вызваны' if tools_called else 'Не вызваны'}")

                if tools_called:
                    tool_names = [tool.get('function', {}).get('name', 'unknown') if isinstance(tool, dict) else str(tool) for tool in tools_called]
                    print(f"   Список: {', '.join(tool_names)}")

                # Сохраняем в историю
                self.conversation_history.append({
                    'step': self.step,
                    'user': user_message,
                    'agent': agent_response,
                    'tools': tools_called,
                    'timestamp': datetime.now().isoformat()
                })

            except Exception as e:
                print(f"❌ ОШИБКА: {e}")
                break

            # Небольшая пауза между шагами
            await asyncio.sleep(1.5)

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
        # Анализ по качествам для AI разработчика
        print(f"\n🎯 ОЦЕНКА КАЧЕСТВ ДЛЯ AI РАЗРАБОТЧИКА:")

        # Бизнес-фокус - упоминания маркетинга, партнеров, роста
        business_focus = sum(1 for msg in self.conversation_history
                            if any(word in msg['agent'].lower()
                                  for word in ['маркетинг', 'партнер', 'продвижени', 'рост', 'бизнес', 'клиент']))
        print(f"   💼 БИЗНЕС-ФОКУС: {business_focus}/{len(self.conversation_history)} бизнес-ориентированных ответов")

        # Техническая экспертиза - упоминания AI, разработки, технологий
        tech_expertise = sum(1 for msg in self.conversation_history
                            if any(word in msg['agent'].lower()
                                  for word in ['ai', 'разработк', 'технолог', 'алгоритм', 'код']))
        print(f"   🤖 ТЕХНИЧЕСКАЯ ЭКСПЕРТИЗА: {tech_expertise}/{len(self.conversation_history)} технических ответов")

        # Проактивность - предложения действий, инициатива
        proactivity = sum(1 for msg in self.conversation_history
                         if any(word in msg['agent'].lower()
                               for word in ['предлагаю', 'создадим', 'найдем', 'проанализируем', 'давай']))
        print(f"   🚀 ПРОАКТИВНОСТЬ: {proactivity}/{len(self.conversation_history)} проактивных предложений")

        # Исполнение - реальные вызовы инструментов
        execution_score = steps_with_tools
        print(f"   ⚡ ИСПОЛНЕНИЕ: {execution_score}/{len(self.conversation_history)} шагов с реальными действиями")

        # Кастомизация под тариф
        tier_specific_features = {
            "LIGHT": ["задачи", "партнеры", "профиль"],
            "STANDARD": ["маркетинг", "пост", "делегирован"],
            "PREMIUM": ["анализ", "алерты", "автономн"]
        }

        tier_usage = sum(1 for msg in self.conversation_history
                        if any(word in msg['agent'].lower()
                              for word in tier_specific_features.get(self.tier, [])))
        print(f"   🎯 ИСПОЛЬЗОВАНИЕ ФУНКЦИЙ {self.tier.upper()}: {tier_usage}/{len(self.conversation_history)} использований функций тарифа")

        # Итоговая оценка
        total_score = (business_focus + tech_expertise + proactivity + execution_score + tier_usage) / 5
        print(f"\n🏆 ИТОГОВАЯ ОЦЕНКА ДЛЯ {self.tier.upper()}: {total_score:.1f}/15 (макс. 15)")

async def main():
    """Запуск тестов для всех тарифов"""
    tiers = ["LIGHT", "STANDARD", "PREMIUM"]

    for tier in tiers:
        print(f"\n{'='*80}")
        print(f"🚀 НАЧИНАЕМ ТЕСТ ДЛЯ ТАРИФА {tier.upper()}")
        print('='*80)

        tester = LiveDialogTester(tier=tier)
        await tester.run_dialog_test()

        print(f"\n💾 ДИАЛОГ СОХРАНЕН В: conversation_log_{tier.lower()}.json")

        # Сохраняем лог для каждого тарифа
        with open(f'conversation_log_{tier.lower()}.json', 'w', encoding='utf-8') as f:
            json.dump(tester.conversation_history, f, ensure_ascii=False, indent=2)

        # Пауза между тарифами
        if tier != tiers[-1]:
            print(f"\n⏳ ПЕРЕХОД К СЛЕДУЮЩЕМУ ТАРИФУ ЧЕРЕЗ 3 СЕКУНДЫ...")
            await asyncio.sleep(3)

if __name__ == "__main__":
    asyncio.run(main())