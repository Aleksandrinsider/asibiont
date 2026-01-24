#!/usr/bin/env python3
"""
Автоматизированный диалог: AI играет роль пользователя и тестирует агента
"""
import asyncio
import sys
import os
import json
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import init_db, User, Task, Session, Subscription, SubscriptionTier, SubscriptionTier
from config import DATABASE_URL, DEEPSEEK_API_KEY, DEEPSEEK_MODEL
import logging
import aiohttp

logging.basicConfig(level=logging.INFO)

class ConversationalAITester:
    """Класс для тестирования агента через диалог AI с AI"""

    def __init__(self):
        self.user_id = 123456789
        self.conversation_history = []
        self.test_results = []

    async def generate_user_message(self, agent_response=None, iteration=1):
        """AI генерирует сообщение пользователя на основе ответа агента"""
        system_prompt = f"""
        Ты - пользователь, который тестирует AI агента управления задачами.
        Твоя цель - вести естественный диалог и проверить все возможности агента.

        Текущая итерация: {iteration}/20

        Возможности агента для проверки:
        1. Создание задач с разными временными параметрами
        2. Просмотр и управление задачами (активные, просроченные, завершенные)
        3. Редактирование задач (изменение времени, приоритета, статуса)
        4. Работа с напоминаниями
        5. Планирование дня/недели
        6. Делегирование задач контактам
        7. Управление профилем (цели, навыки, интересы)
        8. Поиск и анализ контактов
        9. Общие вопросы о возможностях
        10. Обработка ошибок и непонятных запросов

        Правила диалога:
        - Веди естественный разговор, как реальный пользователь
        - Задавай уточняющие вопросы
        - Проверяй разные сценарии использования
        - Не повторяйся слишком часто
        - Проявляй интерес к разным функциям
        - Иногда совершай ошибки в формулировках
        - Реагируй на ответы агента

        История диалога:
        {chr(10).join([f"{'Пользователь' if msg['role'] == 'user' else 'Агент'}: {msg['content'][:100]}..." for msg in self.conversation_history[-6:]])}
        """

        user_prompt = ""
        if agent_response:
            user_prompt = f"Агент ответил: '{agent_response}'\n\nЧто ты скажешь в ответ? Сделай сообщение естественным и проверь какую-то функцию агента."
        else:
            user_prompt = "Начни диалог с агентом. Спроси о его возможностях или создай первую задачу."

        try:
            url = "https://api.deepseek.com/v1/chat/completions"
            headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]

            data = {
                "model": DEEPSEEK_MODEL,
                "messages": messages,
                "temperature": 0.9,  # Высокая креативность для разнообразия
                "max_tokens": 200
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    if response.status == 200:
                        result = await response.json()
                        message = result["choices"][0]["message"]["content"].strip()
                        # Очищаем от лишних символов
                        message = message.strip('"').strip("'")
                        return message
                    else:
                        print(f"❌ Ошибка API: {response.status}")
                        return self._get_fallback_message(iteration)
        except Exception as e:
            print(f"❌ Ошибка генерации: {e}")
            return self._get_fallback_message(iteration)

    def _get_fallback_message(self, iteration):
        """Резервные сообщения для разных итераций"""
        fallbacks = [
            "Привет! Расскажи, что ты умеешь делать?",
            "Создай задачу: позвонить врачу завтра в 10 утра",
            "Покажи все мои активные задачи",
            "Напомни мне через 5 минут выключить чайник",
            "Измени время задачи 'позвонить врачу' на 11 утра",
            "Заверши задачу 'позвонить врачу'",
            "Создай повторяющуюся задачу 'поливать цветы' каждый день в 9 утра",
            "Помоги спланировать мой день",
            "Расскажи о делегировании задач",
            "Покажи просроченные задачи",
            "Измени приоритет задачи на высокий",
            "Удали все выполненные задачи",
            "Обнови мой профиль - добавь цель 'увеличить доход'",
            "Найди контакты с похожими интересами",
            "Создай задачу на неделю вперед",
            "Напомни о встрече за час",
            "Покажи статистику выполненных задач",
            "Помоги с планированием на месяц",
            "Создай задачу без времени",
            "Что ты думаешь о моей продуктивности?"
        ]
        return fallbacks[min(iteration-1, len(fallbacks)-1)]

    def _analyze_agent_response(self, user_message, agent_response, iteration):
        """Анализирует ответ агента"""
        analysis = {
            "iteration": iteration,
            "user_message": user_message,
            "agent_response": agent_response,
            "issues": [],
            "features_tested": [],
            "quality_score": 0
        }

        if not agent_response or len(agent_response.strip()) < 10:
            analysis["issues"].append("Слишком короткий ответ")
            return analysis

        response_lower = agent_response.lower()

        # Определяем протестированные функции
        if any(word in response_lower for word in ["созда", "задач", "напомн"]):
            analysis["features_tested"].append("Создание задач")
        if any(word in response_lower for word in ["покаж", "список", "активн"]):
            analysis["features_tested"].append("Просмотр задач")
        if any(word in response_lower for word in ["измен", "перенес", "обнов"]):
            analysis["features_tested"].append("Редактирование задач")
        if any(word in response_lower for word in ["заверш", "выполн", "готово"]):
            analysis["features_tested"].append("Завершение задач")
        if any(word in response_lower for word in ["планир", "расписан", "график"]):
            analysis["features_tested"].append("Планирование")
        if any(word in response_lower for word in ["делегир", "контакт", "@"]):
            analysis["features_tested"].append("Делегирование")
        if any(word in response_lower for word in ["профиль", "цель", "навык"]):
            analysis["features_tested"].append("Управление профилем")
        if any(word in response_lower for word in ["статистик", "анализ"]):
            analysis["features_tested"].append("Аналитика")

        # Проверки качества
        word_count = len(agent_response.split())
        if word_count < 5:
            analysis["issues"].append("Очень короткий ответ")
        elif word_count > 200:
            analysis["issues"].append("Слишком длинный ответ")

        # Проверка естественности
        if agent_response.count("!") > 3:
            analysis["issues"].append("Слишком много восклицательных знаков")

        # Проверка релевантности
        user_words = set(user_message.lower().split())
        agent_words = set(response_lower.split())
        common_words = user_words.intersection(agent_words)
        if len(common_words) == 0:
            analysis["issues"].append("Ответ не связан с вопросом")

        # Качество ответа
        base_score = 10
        base_score -= len(analysis["issues"]) * 2
        base_score += len(analysis["features_tested"])
        analysis["quality_score"] = max(0, min(10, base_score))

        return analysis

    async def run_conversation_test(self, iterations=20):
        """Запускает диалог на заданное количество итераций"""
        print("🤖🤖 ЗАПУСК КОНВЕРСАЦИОННОГО ТЕСТИРОВАНИЯ AI-АГЕНТА")
        print("=" * 70)

        # Инициализация БД
        print("📊 Инициализация базы данных...")
        init_db()

        # Создаем активную подписку для теста
        session = Session()
        user = session.query(User).filter_by(telegram_id=self.user_id).first()
        if not user:
            user = User(
                telegram_id=self.user_id,
                username="test_ai_user",
                timezone="Europe/Moscow"
            )
            session.add(user)
            session.commit()

        # Создаем активную подписку
        subscription = session.query(Subscription).filter_by(user_id=user.id, status="active").first()
        if not subscription:
            subscription = Subscription(
                user_id=user.id,
                telegram_id=self.user_id,
                tier=SubscriptionTier.BRONZE,
                status="active",
                end_date=None  # Бессрочная для теста
            )
            session.add(subscription)
            session.commit()
            print("✅ Создана активная подписка для теста")

        session.close()

        print(f"📝 Будет проведено {iterations} итераций диалога")
        print()

        # Начинаем диалог
        for i in range(1, iterations + 1):
            print(f"\n🔄 Итерация {i}/{iterations}")

            # Генерируем сообщение пользователя
            if i == 1:
                user_message = await self.generate_user_message(iteration=i)
            else:
                last_agent_response = self.conversation_history[-1]["content"] if self.conversation_history else ""
                user_message = await self.generate_user_message(last_agent_response, i)

            print(f"👤 Пользователь: {user_message}")

            # Получаем ответ агента
            try:
                agent_response = await chat_with_ai(
                    user_message,
                    context=self.conversation_history[-10:],  # Последние 10 сообщений
                    user_id=self.user_id
                )

                print(f"🤖 Агент: {agent_response[:150]}{'...' if len(agent_response) > 150 else ''}")

                # Анализируем ответ
                analysis = self._analyze_agent_response(user_message, agent_response, i)
                self.test_results.append(analysis)

                print(f"📊 Качество: {analysis['quality_score']}/10")
                if analysis['features_tested']:
                    print(f"🔧 Протестировано: {', '.join(analysis['features_tested'])}")
                if analysis['issues']:
                    print(f"⚠️  Проблемы: {', '.join(analysis['issues'])}")

                # Сохраняем в истории
                self.conversation_history.append({"role": "user", "content": user_message})
                self.conversation_history.append({"role": "assistant", "content": agent_response})

                # Ограничиваем историю
                if len(self.conversation_history) > 40:
                    self.conversation_history = self.conversation_history[-40:]

            except Exception as e:
                print(f"❌ Ошибка в итерации {i}: {e}")
                self.test_results.append({
                    "iteration": i,
                    "user_message": user_message,
                    "agent_response": None,
                    "issues": ["Ошибка выполнения"],
                    "features_tested": [],
                    "quality_score": 0
                })

            # Небольшая пауза между итерациями
            await asyncio.sleep(2)

        # Итоговый анализ
        self.print_final_report()

    def print_final_report(self):
        """Выводит итоговый отчет о тестировании"""
        print("\n" + "=" * 70)
        print("📊 ИТОГОВЫЙ ОТЧЕТ КОНВЕРСАЦИОННОГО ТЕСТИРОВАНИЯ")
        print("=" * 70)

        total_iterations = len(self.test_results)
        successful_iterations = len([r for r in self.test_results if r.get("agent_response")])
        avg_quality = sum(r["quality_score"] for r in self.test_results) / total_iterations

        print(f"Всего итераций: {total_iterations}")
        print(f"Успешных ответов: {successful_iterations}")
        print(f"Среднее качество ответов: {avg_quality:.1f}/10")
        # Сбор всех протестированных функций
        all_features = set()
        all_issues = []
        for result in self.test_results:
            all_features.update(result.get("features_tested", []))
            all_issues.extend(result.get("issues", []))

        print(f"\n🔧 Протестированные функции ({len(all_features)}):")
        for feature in sorted(all_features):
            count = sum(1 for r in self.test_results if feature in r.get("features_tested", []))
            print(f"  • {feature}: {count} раз")

        if all_issues:
            print(f"\n⚠️  Найденные проблемы ({len(all_issues)}):")
            from collections import Counter
            issue_counts = Counter(all_issues)
            for issue, count in issue_counts.most_common():
                print(f"  • {issue}: {count} раз")

        # Оценка готовности к продакшену
        production_score = min(10, avg_quality + len(all_features) * 0.5 - len(set(all_issues)) * 0.3)
        print(f"Оценка готовности к продакшену: {production_score:.1f}/10")
        if production_score >= 8:
            print("🎉 Агент готов к продакшену!")
        elif production_score >= 6:
            print("⚠️  Агент почти готов, нужны небольшие доработки")
        else:
            print("❌ Агент нуждается в доработках перед продакшеном")

async def main():
    """Главная функция"""
    tester = ConversationalAITester()

    # Запускаем тест с 20 итерациями
    await tester.run_conversation_test(iterations=20)

    print("\n✅ Конверсационное тестирование завершено!")

if __name__ == "__main__":
    asyncio.run(main())