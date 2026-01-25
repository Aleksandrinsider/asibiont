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
        self.db_state_before = {}  # Состояние БД перед каждым запросом
        self.db_state_after = {}   # Состояние БД после ответа агента

    def check_database_state(self, phase="before"):
        """Проверяет текущее состояние задач в БД"""
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=self.user_id).first()
            if not user:
                return {"error": "User not found"}
            
            tasks = session.query(Task).filter_by(user_id=user.id).all()
            state = {
                "total_tasks": len(tasks),
                "active_tasks": len([t for t in tasks if t.status not in ["completed", "deleted"]]),
                "completed_tasks": len([t for t in tasks if t.status == "completed"]),
                "deleted_tasks": len([t for t in tasks if t.status == "deleted"]),
                "tasks": [
                    {
                        "id": t.id,
                        "title": t.title,
                        "status": t.status,
                        "reminder_time": t.reminder_time.isoformat() if t.reminder_time else None,
                        "created_at": t.created_at.isoformat() if t.created_at else None
                    } for t in tasks
                ]
            }
            
            if phase == "before":
                self.db_state_before = state
            else:
                self.db_state_after = state
                
            return state
        finally:
            session.close()

    def analyze_database_changes(self, user_message, agent_response):
        """Анализирует изменения в БД после ответа агента"""
        changes = {
            "tasks_created": 0,
            "tasks_deleted": 0,
            "tasks_completed": 0,
            "tasks_edited": 0,
            "new_tasks": [],
            "deleted_tasks": [],
            "issues": []
        }
        
        if not self.db_state_before or not self.db_state_after:
            return changes
            
        before = self.db_state_before
        after = self.db_state_after
        
        # Находим новые задачи
        before_ids = {t["id"] for t in before["tasks"]}
        after_ids = {t["id"] for t in after["tasks"]}
        
        new_task_ids = after_ids - before_ids
        deleted_task_ids = before_ids - after_ids
        
        changes["tasks_created"] = len(new_task_ids)
        changes["tasks_deleted"] = len(deleted_task_ids)
        
        # Детали новых задач
        for task in after["tasks"]:
            if task["id"] in new_task_ids:
                changes["new_tasks"].append(task)
                
        # Детали удаленных задач
        for task in before["tasks"]:
            if task["id"] in deleted_task_ids:
                changes["deleted_tasks"].append(task)
        
        # Проверяем изменения статуса
        for after_task in after["tasks"]:
            before_task = next((t for t in before["tasks"] if t["id"] == after_task["id"]), None)
            if before_task:
                if before_task["status"] != after_task["status"]:
                    if after_task["status"] == "completed":
                        changes["tasks_completed"] += 1
                    elif after_task["status"] == "deleted":
                        changes["tasks_deleted"] += 1
                    else:
                        changes["tasks_edited"] += 1
        
        # Анализируем проблемы
        user_lower = user_message.lower()
        response_lower = agent_response.lower() if agent_response else ""
        
        # Если пользователь просит создать задачу без времени, агент должен уточнить
        if any(word in user_lower for word in ["создай", "напомни", "задач"]) and any(word in user_lower for word in ["без времени", "без времени"]):
            if changes["tasks_created"] > 0 and not any(word in response_lower for word in ["когда", "время", "уточни", "во сколько"]):
                changes["issues"].append("Агент создал задачу без уточнения времени")
        
        # Если пользователь просит удалить задачу, агент должен уточнить причину
        if any(word in user_lower for word in ["удали", "удалить", "delete"]):
            if changes["tasks_deleted"] > 0 and not any(word in response_lower for word in ["почему", "причина", "зачем"]):
                changes["issues"].append("Агент удалил задачу без уточнения причины")
        
        # Проверяем дублирование задач
        if changes["tasks_created"] > 1:
            changes["issues"].append(f"Агент создал {changes['tasks_created']} задачи вместо одной")
        
        return changes

    async def generate_user_message(self, agent_response=None, iteration=1):
        """AI генерирует сообщение пользователя на основе ответа агента"""
        system_prompt = f"""
        Ты - пользователь, который тестирует AI агента управления задачами.
        Твоя цель - вести естественный диалог и проверить все возможности агента.

        Текущая итерация: {iteration}/20

        Возможности агента для проверки:
        1. Создание задач с разными временными параметрами (ОБЯЗАТЕЛЬНО проверять уточнение времени!)
        2. Создание задач БЕЗ времени (агент ДОЛЖЕН уточнить время)
        3. Просмотр и управление задачами (активные, просроченные, завершенные)
        4. Редактирование задач (изменение времени, приоритета, статуса)
        5. Удаление задач (агент ДОЛЖЕН уточнить причину удаления)
        6. Работа с напоминаниями
        7. Планирование дня/недели
        8. Делегирование задач контактам
        9. Управление профилем (цели, навыки, интересы)
        10. Поиск и анализ контактов
        11. Обработка ошибок и непонятных запросов

        КРИТИЧНЫЕ ТРЕБОВАНИЯ:
        - Если просишь создать задачу БЕЗ времени - агент ОБЯЗАТЕЛЬНО должен уточнить время
        - Если просишь удалить задачу - агент ОБЯЗАТЕЛЬНО должен уточнить причину
        - Проверяй, что агент не создает дубликаты задач
        - Проверяй, что агент не удаляет задачи без причины

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
            "Создай задачу без времени: купить продукты",  # Тест на уточнение времени
            "Напомни мне через 5 минут выключить чайник",
            "Создай задачу: заказать продукты на Озон",  # Тест на уточнение времени
            "Покажи все мои активные задачи",
            "Измени время задачи 'позвонить врачу' на 11 утра",
            "Заверши задачу 'позвонить врачу'",
            "Удали задачу 'купить продукты'",  # Тест на уточнение причины
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
            "Создай задачу без времени: проверить почту",  # Еще тест на уточнение
            "Удали задачу 'проверить почту'",  # Тест на причину удаления
            "Создай задачу 'сделать отчет' без времени",  # Тест на уточнение
            "Удали задачу 'сделать отчет' потому что она не нужна",  # Тест на причину
            "Что ты думаешь о моей продуктивности?"
        ]
        return fallbacks[min(iteration-1, len(fallbacks)-1)]

    def _analyze_agent_response(self, user_message, agent_response, iteration, db_changes=None):
        """Анализирует ответ агента"""
        analysis = {
            "iteration": iteration,
            "user_message": user_message,
            "agent_response": agent_response,
            "issues": [],
            "features_tested": [],
            "quality_score": 0,
            "db_changes": db_changes or {}
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
        if word_count < 3:  # Уменьшили порог с 5 до 3
            analysis["issues"].append("Очень короткий ответ")
        elif word_count > 80:  # Строже порог с 100 до 80
            analysis["issues"].append("Слишком длинный ответ")

        # Проверка естественности
        if agent_response.count("!") > 3:
            analysis["issues"].append("Слишком много восклицательных знаков")

        # Проверка релевантности - улучшенная логика
        user_keywords = [word for word in user_message.lower().split() if len(word) > 3]  # Только значимые слова
        agent_keywords = [word for word in response_lower.split() if len(word) > 3]
        common_keywords = set(user_keywords).intersection(set(agent_keywords))
        
        # Более строгая проверка: хотя бы 1 общее ключевое слово ИЛИ ответ содержит задачу/время/действие
        has_relevance = (
            len(common_keywords) > 0 or
            any(keyword in response_lower for keyword in ["задач", "время", "созда", "измен", "покаж", "список", "сегодня", "завтра", "напомин", "встреч", "перенес", "удали", "добави", "выполни"])
        )
        
        if not has_relevance:
            analysis["issues"].append("Ответ не связан с вопросом")

        # Качество ответа
        base_score = 10
        base_score -= len(analysis["issues"]) * 2
        base_score += len(analysis["features_tested"])
        
        # Штрафы за проблемы с БД
        if db_changes:
            base_score -= len(db_changes.get("issues", [])) * 3  # Строгий штраф за проблемы с исполнением
        
        analysis["quality_score"] = max(0, min(10, base_score))

        return analysis

    async def run_conversation_test(self, iterations=20):
        """Запускает диалог на заданное количество итераций"""
        print("🤖🤖 ЗАПУСК КОНВЕРСАЦИОННОГО ТЕСТИРОВАНИЯ AI-АГЕНТА")
        print("=" * 70)

        # Инициализация БД
        print("📊 Инициализация базы данных...")
        init_db()
        
        # Очищаем старые задачи для чистого теста
        session = Session()
        user = session.query(User).filter_by(telegram_id=self.user_id).first()
        if user:
            session.query(Task).filter_by(user_id=user.id).delete()
            session.commit()
            print("🧹 Очищены старые задачи пользователя")
        session.close()

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
                # Проверяем состояние БД перед запросом
                self.check_database_state("before")
                
                agent_response = await chat_with_ai(
                    user_message,
                    context=self.conversation_history[-10:],  # Последние 10 сообщений
                    user_id=self.user_id
                )

                # Проверяем состояние БД после ответа
                self.check_database_state("after")
                
                # Анализируем изменения в БД
                db_changes = self.analyze_database_changes(user_message, agent_response)

                print(f"🤖 Агент: {agent_response[:150]}{'...' if len(agent_response) > 150 else ''}")

                # Анализируем ответ
                analysis = self._analyze_agent_response(user_message, agent_response, i, db_changes)
                
                # Добавляем проблемы из БД в анализ
                analysis["issues"].extend(db_changes.get("issues", []))
                
                # Логируем проблемы для анализа
                if analysis["issues"]:
                    print(f"⚠️  Итерация {i}: Проблемы - {', '.join(analysis['issues'])}")
                    print(f"   Вопрос: {user_message[:100]}...")
                    print(f"   Ответ: {agent_response[:100]}...")
                    print()
                self.test_results.append(analysis)

                print(f"📊 Качество: {analysis['quality_score']}/10")
                if analysis['features_tested']:
                    print(f"🔧 Протестировано: {', '.join(analysis['features_tested'])}")
                if db_changes.get("tasks_created", 0) > 0:
                    print(f"✅ Создано задач: {db_changes['tasks_created']}")
                if db_changes.get("tasks_deleted", 0) > 0:
                    print(f"🗑️  Удалено задач: {db_changes['tasks_deleted']}")
                if db_changes.get("tasks_completed", 0) > 0:
                    print(f"✔️  Завершено задач: {db_changes['tasks_completed']}")
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

        # Считаем изменения в БД
        total_tasks_created = sum(r.get("db_changes", {}).get("tasks_created", 0) for r in self.test_results)
        total_tasks_deleted = sum(r.get("db_changes", {}).get("tasks_deleted", 0) for r in self.test_results)
        total_tasks_completed = sum(r.get("db_changes", {}).get("tasks_completed", 0) for r in self.test_results)

        print(f"Всего итераций: {total_iterations}")
        print(f"Успешных ответов: {successful_iterations}")
        print(f"Среднее качество ответов: {avg_quality:.1f}/10")
        print(f"Всего создано задач: {total_tasks_created}")
        print(f"Всего удалено задач: {total_tasks_deleted}")
        print(f"Всего завершено задач: {total_tasks_completed}")
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
        db_actions_score = min(5, (total_tasks_created + total_tasks_deleted + total_tasks_completed) * 0.1)  # До 5 баллов за действия с БД
        production_score = min(10, avg_quality + len(all_features) * 0.5 - len(set(all_issues)) * 0.3 + db_actions_score)
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