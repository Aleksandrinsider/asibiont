import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import json
from self_generating_agent import SelfGeneratingAgent
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
import aiohttp

class LiveDialogueTester:
    """Тестер живого диалога между AI-агентом и AI-пользователем"""

    def __init__(self):
        self.agent = SelfGeneratingAgent()
        self.user_id = 123456789
        self.conversation_history = []
        self.max_turns = 20

    async def generate_user_message(self, context):
        """AI генерирует следующее сообщение пользователя"""

        system_prompt = """Ты - обычный пользователь Telegram-бота для управления задачами.
Ты ведешь естественный разговор с AI-ассистентом ASI Biont.

ПРАВИЛА ПОВЕДЕНИЯ:
- Будь естественным и разговорным
- Задавай вопросы о возможностях бота
- Создавай и управляй задачами
- Ищи партнеров и контакты
- Обновляй профиль
- Иногда просто болтай
- Реагируй на ответы ассистента

ТИПИЧНЫЕ ЗАПРОСЫ ПОЛЬЗОВАТЕЛЯ:
- Создание задач: "Создай задачу позвонить маме завтра в 10", "Напомни купить продукты"
- Управление задачами: "Покажи мои задачи", "Готово, купил продукты", "Перенеси задачу на вечер"
- Поиск контактов: "Найди партнеров для бега", "Кто может помочь с Python?"
- Профиль: "Обнови мой профиль: люблю программирование", "Покажи мой профиль"
- Общение: "Привет", "Что ты умеешь?", "Расскажи о себе"

КОНТЕКСТ РАЗГОВОРА:
{context}

Сгенерируй СЛЕДУЮЩЕЕ СООБЩЕНИЕ пользователя в этом разговоре.
Верни ТОЛЬКО текст сообщения, без кавычек и объяснений."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"История разговора:\n{context}\n\nСгенерируй следующее сообщение пользователя:"}
        ]

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
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
                    # Убираем кавычки если они есть
                    message = message.strip('"').strip("'")
                    return message
                else:
                    return "Привет, что ты умеешь делать?"

    async def run_live_dialogue(self):
        """Запуск живого диалога"""

        print("🤖 ЖИВОЙ ДИАЛОГ: AI-агент vs AI-пользователь")
        print("=" * 80)
        print("Агент: ASI Biont (самообучающийся)")
        print("Пользователь: AI-симулятор естественного поведения")
        print("=" * 80)

        context = "Начало разговора. Пользователь только что открыл чат с ботом."

        for turn in range(self.max_turns):
            print(f"\n🔄 ХОД {turn + 1}/{self.max_turns}")
            print("-" * 40)

            # Генерируем сообщение пользователя
            user_message = await self.generate_user_message(context)
            print(f"👤 ПОЛЬЗОВАТЕЛЬ: {user_message}")

            # Агент отвечает
            try:
                agent_response = await self.agent.process_request(user_message, self.user_id)
                print(f"🤖 АГЕНТ: {agent_response[:150]}{'...' if len(agent_response) > 150 else ''}")
            except Exception as e:
                agent_response = f"Извините, произошла ошибка: {str(e)}"
                print(f"❌ ОШИБКА АГЕНТА: {e}")

            # Обновляем контекст
            self.conversation_history.append({
                'user': user_message,
                'agent': agent_response,
                'turn': turn + 1
            })

            context = "\n".join([
                f"{i+1}. П: {msg['user'][:50]}... А: {msg['agent'][:50]}..."
                for i, msg in enumerate(self.conversation_history[-5:])  # Последние 5 сообщений
            ])

            # Проверяем, не пора ли остановиться
            if any(word in user_message.lower() for word in ['пока', 'до свидания', 'стоп', 'хватит']):
                print("\n👋 Пользователь решил закончить разговор")
                break

            # Небольшая пауза между ходами
            await asyncio.sleep(1)

        # Финальный отчет
        await self.generate_final_report()

    async def generate_final_report(self):
        """Генерация финального отчета о диалоге"""

        print("\n" + "=" * 80)
        print("📊 ФИНАЛЬНЫЙ ОТЧЕТ О ЖИВОМ ДИАЛОГЕ")
        print("=" * 80)

        total_turns = len(self.conversation_history)
        successful_responses = sum(1 for msg in self.conversation_history if 'ошибка' not in msg['agent'].lower())
        success_rate = successful_responses / total_turns * 100 if total_turns > 0 else 0

        print(f"Всего ходов: {total_turns}")
        print(f"Успешных ответов: {successful_responses}")
        print(f"Успешность: {success_rate:.1f}%")
        print(f"Сгенерировано функций: {len(self.agent.generated_functions)}")
        print(f"Выполнено действий: {len(self.agent.execution_history)}")

        # Анализ типов запросов
        request_types = {
            'Создание задач': ['создай', 'напомни', 'задач'],
            'Просмотр задач': ['покажи', 'список', 'мои задачи'],
            'Завершение задач': ['готово', 'сделал', 'завершил'],
            'Поиск контактов': ['найди', 'контакты', 'партнер'],
            'Профиль': ['профиль', 'обнови'],
            'Общение': ['привет', 'умеешь', 'расскажи'],
            'Другое': []
        }

        type_counts = {t: 0 for t in request_types}
        for msg in self.conversation_history:
            user_msg = msg['user'].lower()
            categorized = False
            for req_type, keywords in request_types.items():
                if req_type != 'Другое' and any(k in user_msg for k in keywords):
                    type_counts[req_type] += 1
                    categorized = True
                    break
            if not categorized:
                type_counts['Другое'] += 1

        print("\n📈 РАСПРЕДЕЛЕНИЕ ЗАПРОСОВ:")
        for req_type, count in type_counts.items():
            if count > 0:
                print(f"  {req_type}: {count}")

        print("\n🎯 ВЫВОД:")
        if success_rate >= 90:
            print("🏆 ОТЛИЧНО! Агент успешно ведет естественный диалог!")
        elif success_rate >= 75:
            print("✅ ХОРОШО! Агент работает хорошо в диалоге")
        else:
            print("⚠️  ТРЕБУЕТСЯ ДОРАБОТКА! Агент нуждается в улучшениях")

        print(f"\nСамообучающийся агент показал {success_rate:.1f}% успешности в живом диалоге!")

        # Показываем примеры диалога
        print("\n💬 ПРИМЕРЫ ДИАЛОГА:")
        for i, msg in enumerate(self.conversation_history[:5]):
            print(f"{i+1}. П: {msg['user'][:40]}...")
            print(f"   А: {msg['agent'][:40]}...")

async def main():
    """Основная функция"""
    tester = LiveDialogueTester()
    await tester.run_live_dialogue()

if __name__ == "__main__":
    asyncio.run(main())