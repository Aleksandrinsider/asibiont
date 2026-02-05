"""
Тест живого диалога агента с пользователем.
Использует AI для генерации сообщений пользователя.
Проводит 5 итераций диалога и анализирует результаты.
Оптимизирован для скорости и надежности.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Any
import aiohttp
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from models import Session, User, UserProfile
from ai_integration.chat import chat_with_ai
from ai_integration.intent_classifier_ultra_minimal import IntentClassifierUltraMinimal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DialogTester:
    def __init__(self):
        self.conversation_history: List[Dict[str, Any]] = []
        self.commands_executed: Dict[str, int] = {}
        self.data_considered: Dict[str, int] = {}
        self.dialog_quality: List[float] = []

    async def generate_user_message(self, context: str, iteration: int) -> str:
        """Генерирует сообщение пользователя с учетом итерации для разнообразия"""
        try:
            # Разные типы сообщений для тестирования учета контекста
            message_types = {
                1: "Привет! Я хочу начать планировать свои дела. Что ты умеешь?",  # Общее знакомство
                2: "Да, давай добавим чтение книги! Можешь создать задачу \"Прочитать 20 страниц из 'Грокаем алгоритмы'\" на сегодня вечером в 21:00? И напомни мне за полчаса, пожалуйста.",  # Создание задачи
                3: "Спасибо за напоминание, вечером обязательно почитаю! Насчёт целей — да, я как раз хочу глубже погрузиться в машинное обучение, чтобы через полгода начать делать свои pet-проекты. Можешь помочь с этим?",  # Обновление целей
                4: "Да, отличная идея! Создай задачу \"Установить Python и библиотеки для ML\" на сегодня в 15:00. И напомни за 15 минут, пожалуйста. Кстати, а можешь подсказать, какие именно каналы по ML стоит почитать?",  # Технические рекомендации
                5: "Спасибо за рекомендации! Опыт у меня небольшой — я начинающий программист, в основном работал с Python на базовом уровне. А насчёт поиска единомышленников — это интересно! Можешь найти кого-то, кто тоже начинает погружаться в ML?",  # Поиск партнеров
                6: "Отлично, спасибо! А можешь показать список всех моих задач?",  # Просмотр задач
                7: "Вижу, что у меня есть задача про установку Python. Я её уже выполнил, отметь как готовую.",  # Завершение задачи
                8: "Расскажи подробнее о погоде в Москве сегодня. Что посоветуешь делать в такую погоду?",  # Вопрос о погоде
                9: "А что интересного в новостях сегодня? Есть ли что-то связанное с технологиями или ИИ?",  # Вопрос о новостях
                10: "Перенеси задачу про чтение книги на завтра в 20:00.",  # Перенос задачи
                11: "Обнови мой профиль: я работаю в компании ASI Biont как разработчик.",  # Обновление профиля
                12: "Запомни, что я люблю чай больше кофе.",  # Сохранение предпочтений
                13: "Какие у меня цели на ближайший месяц?",  # Вопрос о целях
                14: "Удалить задачу про чтение книги, я передумал.",  # Удаление задачи
                15: "Найди контакты специалистов по машинному обучению в Москве.",  # Поиск контактов
                16: "Что ты думаешь о моем прогрессе в изучении ML?",  # Анализ прогресса
                17: "Создай задачу на завтра: \"Посмотреть курс по нейронным сетям\" в 18:00.",  # Создание новой задачи
                18: "А можешь проанализировать мои текущие задачи и дать советы?",  # Анализ задач
                19: "Спасибо за все советы! Что еще ты можешь предложить для развития в ИИ?",  # Общие рекомендации
                20: "Покажи мой профиль, что у меня там записано?",  # Просмотр профиля
            }

            # Если итерация в списке, используем предопределенное сообщение
            if iteration in message_types:
                return message_types[iteration]

            # Иначе генерируем через AI
            url = "https://api.deepseek.com/v1/chat/completions"
            headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

            system_prompt = """Ты - обычный пользователь Telegram бота для управления задачами.
Генерируй естественные сообщения, как будто ты реальный пользователь.
Учитывай контекст разговора и текущую ситуацию пользователя (он в Москве, интересуется программированием и ИИ, хочет учиться ML).
Можешь:
- Создавать задачи ("напомни мне позвонить маме завтра в 15:00")
- Завершать задачи ("готово, позвонил маме")
- Просить список задач ("покажи мои задачи")
- Общаться ("привет", "как дела", "что ты умеешь")
- Задавать вопросы о боте
- Удалять задачи ("удали задачу про маму")
- Переносить задачи ("перенеси звонок маме на послезавтра")
- Обновлять профиль ("я из Москвы, работаю программистом")
- Искать партнеров ("найди единомышленников для стартапа")
- Спрашивать о погоде или новостях
- Обсуждать текущие задачи и цели

Делай сообщения разнообразными и реалистичными. Не повторяйся."""

            user_prompt = f"""История разговора:
{context}

Сгенерируй следующее сообщение пользователя. Сделай его естественным и подходящим по контексту.
Учитывай, что пользователь - начинающий программист из Москвы, интересуется ИИ и ML, у него есть задачи на сегодня."""

            data = {
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.8,
                "max_tokens": 100
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        result = await response.json()
                        message = result["choices"][0]["message"]["content"].strip()
                        return message
                    else:
                        return "Расскажи подробнее о своих возможностях"
        except Exception as e:
            logger.error(f"Error generating user message: {e}")
            return "Привет, что нового?"

    async def analyze_response(self, user_message: str, ai_response: str, intent: str) -> Dict[str, Any]:
        """Анализирует ответ агента с учетом комплексного контекста"""
        analysis: Dict[str, Any] = {
            "user_message": user_message,
            "ai_response": ai_response,
            "intent": intent,
            "commands_detected": [],
            "data_considered": [],
            "context_awareness": {
                "profile_mentioned": False,
                "weather_mentioned": False,
                "news_mentioned": False,
                "tasks_considered": False,
                "time_aware": False,
                "personalization_score": 0,
                "complex_links": {
                    "news_to_tasks": False,  # Связывает ли новости с задачами
                    "tasks_to_goals": False,  # Связывает ли задачи с целями
                    "weather_to_advice": False,  # Учитывает ли погоду в советах
                    "proactive_suggestions": False,  # Предлагает ли инициативные действия
                    "comprehensive_score": 0  # Комплексный балл связей
                }
            },
            "quality_score": 0,
            "issues": []
        }

        ai_lower = ai_response.lower()
        user_lower = user_message.lower()

        # Проверяем, выполнил ли агент команды
        if "создал" in ai_lower or "добавил" in ai_lower:
            analysis["commands_detected"].append("add_task")
        if "завершил" in ai_lower or "готово" in ai_lower:
            analysis["commands_detected"].append("complete_task")
        if "список" in ai_lower or "задачи" in ai_lower:
            analysis["commands_detected"].append("list_tasks")
        if "удалил" in ai_lower:
            analysis["commands_detected"].append("delete_task")
        if "перенес" in ai_lower:
            analysis["commands_detected"].append("reschedule_task")
        if "обновил" in ai_lower or "профиль" in ai_lower:
            analysis["commands_detected"].append("update_profile")
        if "единомышленник" in ai_lower or "партнер" in ai_lower:
            analysis["commands_detected"].append("find_partners")

        # Проверяем учет данных
        if "москва" in ai_lower or "программист" in ai_lower or "ии" in ai_lower or "цель" in ai_lower or "ml" in ai_lower:
            analysis["data_considered"].append("profile_data")
            analysis["context_awareness"]["profile_mentioned"] = True
        if "погода" in ai_lower or "мороз" in ai_lower or "температура" in ai_lower or "°c" in ai_response or "холод" in ai_lower:
            analysis["data_considered"].append("weather")
            analysis["context_awareness"]["weather_mentioned"] = True
        if "новост" in ai_lower or "событи" in ai_lower or "желез" in ai_lower or "здоров" in ai_lower:
            analysis["data_considered"].append("news")
            analysis["context_awareness"]["news_mentioned"] = True
        if "задач" in ai_lower or "nlp" in ai_lower or "алгоритм" in ai_lower or "python" in ai_lower or "ml" in ai_lower:
            analysis["data_considered"].append("tasks")
            analysis["context_awareness"]["tasks_considered"] = True

        # Проверяем осведомленность о времени
        if any(word in ai_lower for word in ["утро", "вечер", "сегодня", "завтра", "время", "час", "сейчас"]):
            analysis["context_awareness"]["time_aware"] = True

        # КОМПЛЕКСНЫЙ АНАЛИЗ СВЯЗЕЙ
        # 1. Новости влияют на задачи (например, новость о здоровье -> задача по питанию)
        if ("новост" in ai_lower or "желез" in ai_lower) and ("задач" in ai_lower or "питани" in ai_lower or "здоров" in ai_lower):
            analysis["context_awareness"]["complex_links"]["news_to_tasks"] = True

        # 2. Задачи связаны с целями (например, задача по ML -> цель в машинном обучении)
        if ("задач" in ai_lower or "ml" in ai_lower or "алгоритм" in ai_lower) and ("цель" in ai_lower or "погруз" in ai_lower or "проект" in ai_lower):
            analysis["context_awareness"]["complex_links"]["tasks_to_goals"] = True

        # 3. Погода учитывается в советах (например, холод -> теплые напитки, питание)
        if ("погода" in ai_lower or "холод" in ai_lower or "мороз" in ai_lower) and ("питани" in ai_lower or "напит" in ai_lower or "тепло" in ai_lower):
            analysis["context_awareness"]["complex_links"]["weather_to_advice"] = True

        # 4. Проактивные предложения (инициативные действия без прямого запроса)
        if any(phrase in ai_lower for phrase in ["предлагаю", "давай", "можешь", "стоит", "рекомендую"]) and not any(word in user_lower for word in ["создай", "помоги", "скажи"]):
            analysis["context_awareness"]["complex_links"]["proactive_suggestions"] = True

        # Комплексный балл связей
        complex_score = sum([
            analysis["context_awareness"]["complex_links"]["news_to_tasks"],
            analysis["context_awareness"]["complex_links"]["tasks_to_goals"],
            analysis["context_awareness"]["complex_links"]["weather_to_advice"],
            analysis["context_awareness"]["complex_links"]["proactive_suggestions"]
        ])
        analysis["context_awareness"]["complex_links"]["comprehensive_score"] = complex_score

        # Оцениваем персонализацию (расширенная версия)
        personalization_score = 0
        if analysis["context_awareness"]["profile_mentioned"]:
            personalization_score += 1
        if analysis["context_awareness"]["weather_mentioned"]:
            personalization_score += 1
        if analysis["context_awareness"]["tasks_considered"]:
            personalization_score += 1
        if analysis["context_awareness"]["time_aware"]:
            personalization_score += 1
        # Бонус за комплексные связи
        personalization_score += complex_score
        analysis["context_awareness"]["personalization_score"] = min(personalization_score, 8)  # макс 8/8

        # Оцениваем качество диалога
        quality_score = 5  # базовый балл

        # Штрафы
        if len(ai_response) < 10:
            quality_score -= 2
            analysis["issues"].append("слишком короткий ответ")
        if "время" in ai_response.lower() and len([w for w in ai_response.lower().split() if "время" in w]) > 1:
            quality_score -= 1
            analysis["issues"].append("повторение времени")
        if len(ai_response.split()) > 250:
            quality_score -= 1
            analysis["issues"].append("слишком длинный ответ")
        if not any(char in ai_response for char in ['!', '?', '.']):
            quality_score -= 1
            analysis["issues"].append("нет пунктуации")

        # Бонусы за персонализацию и комплексность
        quality_score += min(personalization_score, 3)  # до +3 за учет контекста и связей

        analysis["quality_score"] = max(0, quality_score)

        return analysis

    async def run_test(self, user_id: int = 1, iterations: int = 20) -> None:
        """Запускает тест диалога"""
        logger.info("Starting live dialog test...")

        # Отключаем фоновые обновления для скорости
        from ai_integration import utils
        original_refresh_weather = utils.refresh_weather_cache_async
        original_refresh_news = utils.refresh_news_cache_async
        utils.refresh_weather_cache_async = lambda *args, **kwargs: None
        utils.refresh_news_cache_async = lambda *args, **kwargs: None

        try:
            db_session = Session()
            try:
                # Получаем или создаем тестового пользователя
                user = db_session.query(User).filter_by(telegram_id=user_id).first()
                if not user:
                    user = User(telegram_id=user_id, username="test_user")
                    db_session.add(user)
                    db_session.commit()

                # Создаем профиль если нет
                profile = db_session.query(UserProfile).filter_by(user_id=user.id).first()
                if not profile:
                    profile = UserProfile(user_id=user.id, city="Москва", interests="программирование, ИИ")
                    db_session.add(profile)
                    db_session.commit()

                # Создаем подписку STANDARD для теста
                from models import Subscription
                subscription = db_session.query(Subscription).filter_by(user_id=user.id).first()
                if not subscription:
                    subscription = Subscription(
                        user_id=user.id,
                        telegram_id=user.telegram_id,
                        tier="STANDARD",
                        status="active",
                        created_at=datetime.now(timezone.utc)
                    )
                    db_session.add(subscription)
                    db_session.commit()

                context = "Начало разговора."

                for i in range(iterations):
                    logger.info(f"Iteration {i+1}/{iterations}")

                    # Генерируем сообщение пользователя
                    user_message = await self.generate_user_message(context, i+1)
                    logger.info(f"User: {user_message}")

                    # Определяем intent
                    intent = await IntentClassifierUltraMinimal.classify_intent(user_message, user_id)
                    logger.info(f"Intent: {intent}")

                    # Получаем ответ агента
                    ai_result = await chat_with_ai(
                        message=user_message,
                        user_id=user_id,
                        db_session=db_session
                    )
                    ai_response = ai_result.get('response', '') if isinstance(ai_result, dict) else str(ai_result)
                    logger.info(f"AI: {ai_response}")

                    # Анализируем ответ
                    analysis = await self.analyze_response(user_message, ai_response, intent)
                    self.conversation_history.append({
                        "iteration": i+1,
                        "user": user_message,
                        "ai": ai_response,
                        "intent": intent,
                        "analysis": analysis
                    })

                    # Обновляем контекст
                    context += f"\nПользователь: {user_message}\nАгент: {ai_response}"

                    # Сохраняем промежуточные результаты каждые 5 итераций
                    if (i + 1) % 5 == 0:
                        self.save_partial_results(i + 1)

                    # Небольшая пауза
                    await asyncio.sleep(1)

                # Анализируем результаты
                self.analyze_results()

            finally:
                db_session.close()
        finally:
            # Восстанавливаем оригинальные функции
            utils.refresh_weather_cache_async = original_refresh_weather
            utils.refresh_news_cache_async = original_refresh_news

    def save_partial_results(self, iteration: int) -> None:
        """Сохраняет промежуточные результаты"""
        partial_data = {
            "partial_results_up_to_iteration": iteration,
            "conversations": self.conversation_history,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        with open(f"dialog_test_partial_{iteration}.json", "w", encoding="utf-8") as f:
            json.dump(partial_data, f, ensure_ascii=False, indent=2)
        logger.info(f"Partial results saved up to iteration {iteration}")

    def analyze_results(self) -> None:
        """Анализирует результаты теста с учетом учета контекста"""
        logger.info("Analyzing test results...")

        total_quality = 0
        commands_count: Dict[str, int] = {}
        data_count: Dict[str, int] = {}
        issues_count: Dict[str, int] = {}
        context_stats = {
            "profile_mentions": 0,
            "weather_mentions": 0,
            "news_mentions": 0,
            "tasks_considered": 0,
            "time_awareness": 0,
            "total_personalization": 0,
            "complex_links": {
                "news_to_tasks": 0,
                "tasks_to_goals": 0,
                "weather_to_advice": 0,
                "proactive_suggestions": 0,
                "total_comprehensive": 0
            }
        }

        for conv in self.conversation_history:
            analysis = conv["analysis"]
            total_quality += analysis["quality_score"]

            for cmd in analysis["commands_detected"]:
                commands_count[cmd] = commands_count.get(cmd, 0) + 1

            for data in analysis["data_considered"]:
                data_count[data] = data_count.get(data, 0) + 1

            for issue in analysis["issues"]:
                issues_count[issue] = issues_count.get(issue, 0) + 1

            # Статистика учета контекста
            ctx = analysis["context_awareness"]
            if ctx["profile_mentioned"]:
                context_stats["profile_mentions"] += 1
            if ctx["weather_mentioned"]:
                context_stats["weather_mentions"] += 1
            if ctx["news_mentioned"]:
                context_stats["news_mentions"] += 1
            if ctx["tasks_considered"]:
                context_stats["tasks_considered"] += 1
            if ctx["time_aware"]:
                context_stats["time_awareness"] += 1
            context_stats["total_personalization"] += ctx["personalization_score"]

            # Комплексные связи
            complex_links = ctx["complex_links"]
            if complex_links["news_to_tasks"]:
                context_stats["complex_links"]["news_to_tasks"] += 1
            if complex_links["tasks_to_goals"]:
                context_stats["complex_links"]["tasks_to_goals"] += 1
            if complex_links["weather_to_advice"]:
                context_stats["complex_links"]["weather_to_advice"] += 1
            if complex_links["proactive_suggestions"]:
                context_stats["complex_links"]["proactive_suggestions"] += 1
            context_stats["complex_links"]["total_comprehensive"] += complex_links["comprehensive_score"]

        avg_quality = total_quality / len(self.conversation_history) if self.conversation_history else 0
        avg_personalization = context_stats["total_personalization"] / len(self.conversation_history) if self.conversation_history else 0

        logger.info("=== TEST RESULTS ===")
        logger.info(f"Average dialog quality: {avg_quality:.1f}/10")
        logger.info(f"Average personalization score: {avg_personalization:.1f}/8")
        logger.info(f"Commands executed: {commands_count}")
        logger.info(f"Data considered: {data_count}")
        logger.info(f"Context awareness: {context_stats}")
        logger.info(f"Common issues: {issues_count}")

        # Сохраняем результаты в файл
        with open("dialog_test_results.json", "w", encoding="utf-8") as f:
            json.dump({
                "conversations": self.conversation_history,
                "summary": {
                    "average_quality": avg_quality,
                    "average_personalization": avg_personalization,
                    "commands_executed": commands_count,
                    "data_considered": data_count,
                    "context_awareness": context_stats,
                    "issues": issues_count,
                    "total_iterations": len(self.conversation_history)
                }
            }, f, ensure_ascii=False, indent=2)

        logger.info("Results saved to dialog_test_results.json")

async def main():
    tester = DialogTester()
    await tester.run_test(iterations=20)

if __name__ == "__main__":
    asyncio.run(main())