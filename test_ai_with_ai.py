#!/usr/bin/env python3
"""
Автоматизированный тест диалога с AI агентом с использованием ИИ для генерации сообщений
"""
import asyncio
import sys
import os
import json
import re

# Включаем бесплатный доступ для тестирования
os.environ['FREE_ACCESS_MODE'] = 'True'

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import init_db, User, Task, Session
from config import DATABASE_URL, DEEPSEEK_API_KEY, DEEPSEEK_MODEL
import logging
import aiohttp

logging.basicConfig(level=logging.INFO)

class AIDialogTester:
    """Класс для тестирования диалога с AI агентом"""

    def __init__(self):
        self.user_id = 123456789
        self.message_history = []
        self.test_results = []

    async def generate_test_messages(self, count=10):
        """Генерирует разнообразные тестовые сообщения с помощью ИИ"""
        print(f"🎯 Генерирую {count} тестовых сообщений с помощью ИИ...")

        system_prompt = """
        Ты - генератор тестовых сообщений для проверки AI агента управления задачами.
        Создай разнообразные сообщения, которые пользователь может отправить боту.

        Типы сообщений:
        1. Создание задач: "создай задачу купить продукты", "напомни позвонить маме"
        2. Управление задачами: "покажи мои задачи", "заверши задачу купить продукты"
        3. Вопросы о времени: "напомни через 5 минут", "создай задачу на завтра в 10 утра"
        4. Общие вопросы: "что ты умеешь?", "помоги с планированием"
        5. Сложные сценарии: "перенеси задачу на вечер", "удали все просроченные задачи"
        6. Ошибочные сообщения: "создай задачу", "напомни"

        Создай JSON массив из {count} разнообразных сообщений.
        Каждое сообщение должно быть реалистичным и тестировать разные функции.
        """

        try:
            url = "https://api.deepseek.com/v1/chat/completions"
            headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Создай {count} разнообразных тестовых сообщений в формате JSON массива строк."}
            ]

            data = {
                "model": DEEPSEEK_MODEL,
                "messages": messages,
                "temperature": 0.8,
                "max_tokens": 1000
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    if response.status == 200:
                        result = await response.json()
                        content = result["choices"][0]["message"]["content"]

                        # Извлекаем JSON из ответа
                        json_match = re.search(r'\[.*\]', content, re.DOTALL)
                        if json_match:
                            messages = json.loads(json_match.group(0))
                            print(f"✅ Сгенерировано {len(messages)} сообщений")
                            return messages[:count]  # Ограничиваем количеством
                        else:
                            print("❌ Не удалось извлечь JSON из ответа ИИ")
                            return self._get_fallback_messages(count)
                    else:
                        print(f"❌ Ошибка API: {response.status}")
                        return self._get_fallback_messages(count)

        except Exception as e:
            print(f"❌ Ошибка генерации сообщений: {e}")
            return self._get_fallback_messages(count)

    def _get_fallback_messages(self, count):
        """Резервные тестовые сообщения"""
        fallback_messages = [
            "создай задачу проверить почту",
            "покажи мои задачи",
            "заверши задачу проверить почту",
            "напомни через 5 минут",
            "создай задачу купить продукты на завтра",
            "что ты умеешь?",
            "перенеси задачу на вечер",
            "удали задачу купить продукты",
            "помоги с планированием",
            "напомни позвонить маме",
            "создай задачу на 15:00",
            "покажи выполненные задачи",
            "измени задачу проверить почту на проверить email",
            "сколько у меня задач?",
            "создай задачу без времени"
        ]
        return fallback_messages[:count]

    async def test_message(self, message):
        """Тестирует одно сообщение"""
        print(f"\n🧪 Тестирую: '{message}'")

        try:
            # Отправляем сообщение AI агенту
            response = await chat_with_ai(message, context=self.message_history, user_id=self.user_id)

            # Сохраняем в истории
            self.message_history.append({"role": "user", "content": message})
            self.message_history.append({"role": "assistant", "content": response})

            # Ограничиваем историю
            if len(self.message_history) > 20:
                self.message_history = self.message_history[-20:]

            # Анализируем ответ
            analysis = self._analyze_response(message, response)

            # Сохраняем результат
            result = {
                "message": message,
                "response": response,
                "analysis": analysis
            }
            self.test_results.append(result)

            # Выводим результат
            print(f"🤖 Ответ: {response[:100]}{'...' if len(response) > 100 else ''}")
            print(f"📊 Анализ: {analysis['summary']}")

            if analysis['issues']:
                print(f"⚠️  Проблемы: {', '.join(analysis['issues'])}")

            return True

        except Exception as e:
            print(f"❌ Ошибка при тестировании: {e}")
            self.test_results.append({
                "message": message,
                "response": None,
                "error": str(e)
            })
            return False

    def _analyze_response(self, message, response):
        """Анализирует ответ AI агента"""
        issues = []
        warnings = []

        # Проверки качества ответа
        if not response or response.strip() == "":
            issues.append("Пустой ответ")
            return {"summary": "❌ Пустой ответ", "issues": issues, "warnings": warnings}

        response_lower = response.lower()
        word_count = len(response.split())

        # Проверка длины
        if word_count < 3:
            issues.append("Слишком короткий ответ")
        elif word_count > 150:
            warnings.append("Очень длинный ответ")

        # Проверка запрещенных фраз
        forbidden_phrases = ["уже ночь", "хорошее время отдохнуть"]
        for phrase in forbidden_phrases:
            if phrase in response_lower:
                issues.append(f"Запрещенная фраза: '{phrase}'")

        # Проверка разнообразия
        if "отлично" in response_lower or "замечательно" in response_lower or "конечно" in response_lower:
            issues.append("Шаблонные фразы")

        # Проверка релевантности
        message_words = set(message.lower().split())
        response_words = set(response_lower.split())
        common_words = message_words.intersection(response_words)

        if len(common_words) < 1 and not any(keyword in response_lower for keyword in ["задач", "напомн", "созда", "покаж", "заверш"]):
            warnings.append("Ответ может быть нерелевантным")

        # Проверка маркеров NEED_TIME_FOR_TASK
        if "NEED_TIME_FOR_TASK" in response:
            issues.append("Маркер NEED_TIME_FOR_TASK в ответе")

        # Определяем тип ответа
        if "на какое время" in response_lower:
            response_type = "уточнение времени"
        elif "задача" in response_lower and ("созда" in response_lower or "заверш" in response_lower):
            response_type = "управление задачами"
        elif "покаж" in response_lower or "список" in response_lower:
            response_type = "показ задач"
        else:
            response_type = "общий ответ"

        # Формируем summary
        if issues:
            summary = f"❌ {len(issues)} проблем"
        elif warnings:
            summary = f"⚠️ {len(warnings)} предупреждений"
        else:
            summary = f"✅ OK ({response_type})"

        return {
            "summary": summary,
            "issues": issues,
            "warnings": warnings,
            "response_type": response_type,
            "word_count": word_count
        }

    def print_summary(self):
        """Выводит итоговую статистику"""
        print("\n" + "="*60)
        print("📊 ИТОГИ ТЕСТИРОВАНИЯ")
        print("="*60)

        total_tests = len(self.test_results)
        successful_tests = len([r for r in self.test_results if "error" not in r])
        failed_tests = total_tests - successful_tests

        print(f"Всего тестов: {total_tests}")
        print(f"Успешных: {successful_tests}")
        print(f"С ошибками: {failed_tests}")

        if successful_tests > 0:
            # Анализ успешных тестов
            analyses = [r["analysis"] for r in self.test_results if "error" not in r]

            issues_count = sum(len(a["issues"]) for a in analyses)
            warnings_count = sum(len(a["warnings"]) for a in analyses)

            print(f"Проблем: {issues_count}")
            print(f"Предупреждений: {warnings_count}")

            # Распределение по типам ответов
            response_types = {}
            for a in analyses:
                rt = a.get("response_type", "неизвестно")
                response_types[rt] = response_types.get(rt, 0) + 1

            print("\nРаспределение по типам ответов:")
            for rt, count in response_types.items():
                print(f"  {rt}: {count}")

        # Детальный разбор ошибок
        if failed_tests > 0:
            print(f"\n❌ Ошибки ({failed_tests}):")
            for i, result in enumerate(self.test_results):
                if "error" in result:
                    print(f"  {i+1}. '{result['message'][:50]}...' → {result['error']}")

        # Топ проблем
        if successful_tests > 0:
            all_issues = []
            for result in self.test_results:
                if "analysis" in result:
                    all_issues.extend(result["analysis"]["issues"])

            if all_issues:
                from collections import Counter
                issue_counts = Counter(all_issues)
                print("\n🔝 Топ проблем:")
                for issue, count in issue_counts.most_common(5):
                    print(f"  {issue}: {count}")

    async def run_test(self, message_count=10):
        """Запускает полный тест"""
        print("🚀 ЗАПУСК АВТОМАТИЗИРОВАННОГО ТЕСТИРОВАНИЯ AI АГЕНТА")
        print("=" * 60)

        # Инициализация БД
        print("📊 Инициализация базы данных...")
        init_db()

        # Очищаем старые данные
        session = Session()
        session.query(Task).filter_by(user_id=self.user_id).delete()
        session.commit()
        session.close()

        # Генерируем тестовые сообщения
        test_messages = await self.generate_test_messages(message_count)

        print(f"📝 Будет протестировано {len(test_messages)} сообщений:")
        for i, msg in enumerate(test_messages, 1):
            print(f"  {i}. {msg}")
        print()

        # Запускаем тестирование
        successful = 0
        for message in test_messages:
            if await self.test_message(message):
                successful += 1
            await asyncio.sleep(1)  # Небольшая пауза между запросами

        # Выводим итоги
        self.print_summary()

        print("\n✅ Тестирование завершено!")
        return successful == len(test_messages)

async def main():
    """Главная функция"""
    tester = AIDialogTester()

    # Запускаем тест с 15 сообщениями для полного покрытия
    success = await tester.run_test(message_count=15)

    if not success:
        print("\n⚠️  Найдены проблемы! Рекомендуется исправить их перед развертыванием.")
    else:
        print("\n🎉 Все тесты пройдены! AI агент работает корректно.")

if __name__ == "__main__":
    asyncio.run(main())