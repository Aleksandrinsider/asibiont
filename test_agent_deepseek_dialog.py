#!/usr/bin/env python3
"""
Тест-диалог: Агент vs DeepSeek (пользователь)
Агент общается естественно, без заготовок
DeepSeek генерирует реалистичные ответы пользователя
"""
import asyncio
import json
import logging
from datetime import datetime
from models import SessionLocal
from ai_integration.chat import chat_with_ai
from config import DEEPSEEK_API_KEY
import requests

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DialogTester:
    def __init__(self):
        self.conversation_history = []
        self.agent_responses = []
        self.user_messages = []
        self.function_calls = []

    def log_message(self, role, content, function_calls=None):
        """Логируем сообщение с информацией о функциях"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = {
            "timestamp": timestamp,
            "role": role,
            "content": content[:200] + "..." if len(content) > 200 else content
        }

        if function_calls:
            entry["function_calls"] = function_calls

        self.conversation_history.append(entry)
        print(f"\n[{timestamp}] {role.upper()}: {content[:100]}{'...' if len(content) > 100 else ''}")

        if function_calls:
            print(f"    Выполнено функций: {len(function_calls)} - {', '.join(function_calls)}")

    async def get_agent_response(self, user_message, user_id=99999):
        """Получаем ответ агента"""
        try:
            session = SessionLocal()
            response = await chat_with_ai(
                message=user_message,
                user_id=user_id,
                db_session=session,
                message_type="text"
            )
            session.close()

            # Извлекаем информацию о вызванных функциях
            function_calls = []
            if 'tool_calls' in response and response['tool_calls']:
                for tool_call in response['tool_calls']:
                    func_name = tool_call['function']['name']
                    function_calls.append(func_name)

            self.function_calls.extend(function_calls)

            agent_text = response.get('response', 'Ошибка ответа')
            self.agent_responses.append(agent_text)

            return agent_text, function_calls

        except Exception as e:
            logger.error(f"Ошибка агента: {e}")
            return f"Ошибка: {e}", []

    def get_deepseek_user_message(self, conversation_context):
        """DeepSeek генерирует следующее сообщение пользователя с улучшенной логикой"""
        context_summary = "\n".join([
            f"{msg['role']}: {msg['content']}"
            for msg in self.conversation_history[-5:]  # последние 5 сообщений для большего контекста
        ])

        # Определяем фазу диалога для более реалистичных запросов
        turn_count = len([msg for msg in self.conversation_history if msg['role'] == 'user'])
        
        phase_instruction = ""
        if turn_count < 3:
            phase_instruction = "Это начало разговора - генерируй простые запросы типа 'привет', 'что умеешь', 'помоги с задачами'"
        elif turn_count < 8:
            phase_instruction = "Средняя фаза - добавляй уточнения, исправления ошибок, новые запросы на основе предыдущих ответов"
        else:
            phase_instruction = "Поздняя фаза - генерируй сложные запросы, комбинируй темы, проси помощи в комплексных задачах"

        prompt = f"""Ты - обычный пользователь Telegram бота для управления задачами.
Вот история разговора (последние 5 сообщений):
{context_summary}

ФАЗА ДИАЛОГА: {phase_instruction}

Генерируй следующее РЕАЛИСТИЧНОЕ сообщение пользователя. НЕ используй никаких шаблонов или инструкций - просто напиши как обычный человек в чате.

ВАЖНЫЕ ПРАВИЛА:
- Учитывай предыдущие сообщения агента
- Добавляй уточнения если что-то непонятно
- Проси исправить ошибки если агент перепутал
- Комбинируй темы из предыдущих ответов
- Задавай сложные вопросы требующие нескольких инструментов
- Используй естественный язык с опечатками и сокращениями

ПРИМЕРЫ СТИЛЯ:
- "Привет, что можешь делать?"
- "Окей, но ты перепутал задачу - имел в виду ту, что про отчет"
- "Круто, а теперь найди мне партнеров по Python и расскажи про тренды"
- "Не понял, объясни подробнее про маркетинг"
- "Создай задачу на завтра и найди кого-нибудь в помощь"
- "А можешь проанализировать рынок AI-ботов и написать пост?"

Твое сообщение (только текст, без кавычек):"""

        try:
            headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}"}
            data = {
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 150,  # больше токенов для сложных запросов
                "temperature": 0.9  # выше температура для разнообразия
            }

            response = requests.post(
                "https://api.deepseek.com/chat/completions",
                headers=headers,
                json=data,
                timeout=20
            )

            if response.status_code == 200:
                result = response.json()
                user_message = result['choices'][0]['message']['content'].strip()
                # Очищаем от лишних кавычек если есть
                user_message = user_message.strip('"\'')
                self.user_messages.append(user_message)
                return user_message
            else:
                return "Расскажи подробнее про свои возможности"

        except Exception as e:
            logger.error(f"Ошибка DeepSeek: {e}")
            return "Что ещё умеешь?"

        except Exception as e:
            logger.error(f"Ошибка DeepSeek: {e}")
            return "Привет, что можешь?"

    async def run_dialog_test(self, max_turns=5):
        """Запускаем тест-диалог"""
        print("=" * 80)
        print("TEST-DIALOG: Agent vs DeepSeek (user) - STANDARD USER TEST")
        print("=" * 80)

        # Создаем/обновляем СТАНДАРТНОГО пользователя для теста
        session = SessionLocal()
        try:
            from models import User, SubscriptionTier
            user = session.query(User).filter_by(telegram_id=99999).first()
            if not user:
                user = User(
                    telegram_id=99999,
                    username="test_standard_user",
                    subscription_tier=SubscriptionTier.STANDARD
                )
                session.add(user)
                print("✅ Создан СТАНДАРТНЫЙ пользователь для теста")
            else:
                user.subscription_tier = SubscriptionTier.STANDARD
                print("✅ Обновлен пользователь до СТАНДАРТНОГО статуса")
            session.commit()
        except Exception as e:
            print(f"⚠️  Ошибка настройки пользователя: {e}")
        finally:
            session.close()

        # Стартовое сообщение пользователя
        current_user_message = "Привет"

        for turn in range(max_turns):
            print(f"\nTURN {turn + 1}/{max_turns}")
            print("-" * 40)

            # Пользователь говорит
            self.log_message("user", current_user_message)

            # Агент отвечает
            agent_response, functions = await self.get_agent_response(current_user_message)
            self.log_message("agent", agent_response, functions)

            # Если агент вызвал функции, показываем это
            if functions:
                print(f"    Выполнено функций: {len(functions)} - {', '.join(functions)}")

            # Генерируем следующий ответ пользователя
            if turn < max_turns - 1:  # Не генерируем на последнем ходу
                current_user_message = self.get_deepseek_user_message(self.conversation_history)

        # Анализ результатов
        self.analyze_results()

    def analyze_results(self):
        """Анализируем результаты диалога с улучшенной аналитикой"""
        print("\n" + "=" * 80)
        print("АНАЛИЗ РЕЗУЛЬТАТОВ ТЕСТА")
        print("=" * 80)

        print(f"Всего ходов: {len(self.conversation_history) // 2}")
        print(f"Ответов агента: {len(self.agent_responses)}")
        print(f"Сообщений пользователя: {len(self.user_messages)}")
        print(f"Вызовов функций: {len(self.function_calls)}")

        if self.function_calls:
            print(f"Использованные функции: {', '.join(set(self.function_calls))}")
            print(f"Уникальных функций: {len(set(self.function_calls))}/28")

        # Анализ качества ответов
        deep_responses = sum(1 for resp in self.agent_responses if len(resp.split()) > 10)
        print(f"Подробных ответов: {deep_responses}/{len(self.agent_responses)}")

        # Анализ использования инструментов
        tool_usage = len(self.function_calls) / len(self.agent_responses) if self.agent_responses else 0
        print(f"Среднее функций на ответ: {tool_usage:.2f}")

        # Проверка разнообразия функций
        if len(set(self.function_calls)) < 10:
            print("⚠️  МАЛО РАЗНООБРАЗИЯ: Использовано менее 10 разных функций")
        else:
            print("✅ ХОРОШЕЕ РАЗНООБРАЗИЕ: Использовано более 10 разных функций")

        # Проверка премиум-функций
        premium_functions = {'set_contact_alert', 'set_activity_alert', 'research_topic', 'generate_marketing_content', 'publish_to_telegram', 'delegate_task'}
        used_premium = set(self.function_calls) & premium_functions
        print(f"Премиум-функций использовано: {len(used_premium)}/{len(premium_functions)}")

        # Анализ естественности
        natural_responses = sum(1 for resp in self.agent_responses
                              if not any(word in resp.lower() for word in ['отлично', 'понимаю', 'круто', 'хорошо']))
        print(f"Естественных ответов: {natural_responses}/{len(self.agent_responses)}")

        # Анализ многошаговых операций
        multistep_indicators = ['шага', 'сначала', 'затем', 'теперь', 'далее']
        multistep_responses = sum(1 for resp in self.agent_responses
                                if any(indicator in resp.lower() for indicator in multistep_indicators))
        print(f"Ответов с многошаговыми операциями: {multistep_responses}/{len(self.agent_responses)}")

        # Анализ проактивности
        proactive_indicators = ['могу', 'давай', 'предлагаю', 'еще', 'кроме того', 'также']
        proactive_responses = sum(1 for resp in self.agent_responses
                                if any(indicator in resp.lower() for indicator in proactive_indicators))
        print(f"Проактивных ответов: {proactive_responses}/{len(self.agent_responses)}")

        print("\nПОЛНЫЙ ДИАЛОГ:")
        for i, msg in enumerate(self.conversation_history):
            print(f"{i+1}. [{msg['timestamp']}] {msg['role'].upper()}: {msg['content']}")
            if 'function_calls' in msg and msg['function_calls']:
                print(f"    Выполнено функций: {len(msg['function_calls'])} - {', '.join(msg['function_calls'])}")

async def main():
    tester = DialogTester()
    await tester.run_dialog_test(max_turns=5)

if __name__ == "__main__":
    asyncio.run(main())