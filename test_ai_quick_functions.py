#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
БЫСТРОЕ ТЕСТИРОВАНИЕ AI ФУНКЦИЙ
Тестирование основных возможностей агента без создания пользователей в БД
"""

import os
import asyncio
from datetime import datetime

# Устанавливаем переменную окружения для локального тестирования
os.environ['LOCAL'] = '1'

# Импорты проекта
from ai_integration.chat import chat_with_ai

class QuickAITestSuite:
    """Быстрый набор тестов для AI функций"""
    
    def __init__(self):
        self.test_results = []
        self.test_user_id = 12345  # Виртуальный ID для тестов

    async def test_conversation_types(self):
        """Тест различных типов разговора"""
        print("🗣️ Тестирование типов разговора...")
        
        test_cases = [
            ("Привет!", "greeting"),
            ("Как дела?", "greeting"),
            ("сколько времени?", "time_query"),
            ("который час?", "time_query"), 
            ("дай совет", "advice"),
            ("как быть продуктивнее?", "advice"),
            ("создай задачу на завтра", "task_creation"),
            ("покажи мои задачи", "task_list"),
            ("обнови мой профиль", "profile"),
            ("найди контакты", "contacts")
        ]
        
        results = []
        for query, expected_type in test_cases:
            print(f"  💭 '{query}' [{expected_type}]")
            try:
                response = await chat_with_ai(query, [], self.test_user_id)
                success = response is not None and len(str(response)) > 5
                status = "✅" if success else "❌"
                results.append(f"{status} {query} → {str(response)[:60]}...")
            except Exception as e:
                results.append(f"❌ {query} → ERROR: {str(e)[:40]}...")
        
        return "\n".join(results)

    async def test_task_operations(self):
        """Тест операций с задачами"""
        print("📋 Тестирование операций с задачами...")
        
        task_queries = [
            "создай задачу: позвонить маме завтра в 15:00",
            "добавь напоминание: купить молоко через час",
            "запланируй встречу на понедельник в 10:00",
            "напомни мне проверить почту в 18:00",
            "покажи все мои задачи",
            "что у меня на сегодня?",
            "список дел на завтра",
            "удали задачу про молоко",
            "отметь задачу как выполненную"
        ]
        
        results = []
        for query in task_queries:
            print(f"  📝 '{query}'")
            try:
                response = await chat_with_ai(query, [], self.test_user_id)
                # Проверяем что ответ связан с задачами
                task_keywords = ['задач', 'напоминан', 'план', 'дел', 'встреч', 'запланир', 'создал', 'добавил']
                has_task_context = any(keyword in str(response).lower() for keyword in task_keywords)
                status = "✅" if has_task_context else "❌"
                results.append(f"{status} {query[:30]}... → {str(response)[:50]}...")
            except Exception as e:
                results.append(f"❌ {query[:30]}... → ERROR: {str(e)[:30]}...")
        
        return "\n".join(results)

    async def test_profile_operations(self):
        """Тест операций с профилем"""
        print("👤 Тестирование операций с профилем...")
        
        profile_queries = [
            "покажи мой профиль",
            "обнови мои навыки: добавь Python",
            "установи город: Москва",
            "измени рабочее время: 9:00-18:00",
            "добавь интерес: Программирование",
            "обнови информацию о себе"
        ]
        
        results = []
        for query in profile_queries:
            print(f"  👤 '{query}'")
            try:
                response = await chat_with_ai(query, [], self.test_user_id)
                # Проверяем что ответ связан с профилем
                profile_keywords = ['профиль', 'навык', 'город', 'время', 'интерес', 'информаци', 'обновил', 'добавил']
                has_profile_context = any(keyword in str(response).lower() for keyword in profile_keywords)
                status = "✅" if has_profile_context else "❌"
                results.append(f"{status} {query[:25]}... → {str(response)[:50]}...")
            except Exception as e:
                results.append(f"❌ {query[:25]}... → ERROR: {str(e)[:30]}...")
        
        return "\n".join(results)

    async def test_advice_and_conversation(self):
        """Тест советов и общения"""
        print("💡 Тестирование советов и общения...")
        
        advice_queries = [
            "дай совет по продуктивности",
            "как лучше организовать день?",
            "что посоветуешь для эффективности?",
            "помоги с планированием",
            "как управлять временем?",
            "расскажи о себе",
            "что ты умеешь?",
            "спасибо за помощь",
            "пока!"
        ]
        
        results = []
        for query in advice_queries:
            print(f"  💭 '{query}'")
            try:
                response = await chat_with_ai(query, [], self.test_user_id)
                # Проверяем что получен осмысленный ответ
                is_meaningful = response is not None and len(str(response)) > 10
                status = "✅" if is_meaningful else "❌"
                results.append(f"{status} {query[:25]}... → {str(response)[:50]}...")
            except Exception as e:
                results.append(f"❌ {query[:25]}... → ERROR: {str(e)[:30]}...")
        
        return "\n".join(results)

    async def test_edge_cases(self):
        """Тест граничных случаев"""
        print("⚠️ Тестирование граничных случаев...")
        
        edge_cases = [
            "",  # Пустая строка
            "   ",  # Пробелы
            "?",  # Один символ
            "а" * 100,  # Длинная строка
            "123456789",  # Цифры
            "@#$%^&*()",  # Символы
            "🚀🤖✨",  # Эмодзи
            "тест тест тест тест тест",  # Повторения
            "ыыыыыыыы",  # Бессмыслица
            "SELECT * FROM users;",  # SQL
        ]
        
        results = []
        for case in edge_cases:
            print(f"  ⚠️ '{case[:15]}{'...' if len(case) > 15 else ''}'")
            try:
                response = await chat_with_ai(case, [], self.test_user_id)
                # Проверяем что система не упала и дала ответ
                handled_correctly = response is not None and len(str(response)) > 0
                status = "✅" if handled_correctly else "❌"
                results.append(f"{status} '{case[:10]}...' → {str(response)[:40] if response else 'None'}...")
            except Exception as e:
                results.append(f"❌ '{case[:10]}...' → ERROR: {str(e)[:30]}...")
        
        return "\n".join(results)

    async def test_dialogue_continuity(self):
        """Тест непрерывности диалога"""
        print("🔗 Тестирование непрерывности диалога...")
        
        conversation_flow = [
            "Привет!",
            "Как дела?",
            "Создай задачу: позвонить Ивану завтра",
            "А что еще у меня запланировано?",
            "Спасибо! Удачного дня!"
        ]
        
        history = []
        results = []
        
        for i, message in enumerate(conversation_flow):
            print(f"  {i+1}. '{message}'")
            try:
                response = await chat_with_ai(message, history, self.test_user_id)
                
                # Добавляем в историю
                history.append({"role": "user", "content": message})
                history.append({"role": "assistant", "content": str(response)})
                
                # Проверяем адекватность
                is_adequate = response is not None and len(str(response)) > 5
                status = "✅" if is_adequate else "❌"
                results.append(f"{status} Шаг {i+1}: {str(response)[:50]}...")
                
            except Exception as e:
                results.append(f"❌ Шаг {i+1}: ERROR: {str(e)[:40]}...")
        
        return "\n".join(results)

    async def run_all_tests(self):
        """Запуск всех тестов"""
        print("="*80)
        print("🚀 БЫСТРОЕ ТЕСТИРОВАНИЕ AI ФУНКЦИЙ")
        print("="*80)
        
        tests = [
            ("1. Типы разговора", self.test_conversation_types),
            ("2. Операции с задачами", self.test_task_operations),
            ("3. Операции с профилем", self.test_profile_operations),
            ("4. Советы и общение", self.test_advice_and_conversation),
            ("5. Граничные случаи", self.test_edge_cases),
            ("6. Непрерывность диалога", self.test_dialogue_continuity),
        ]
        
        overall_results = []
        
        for test_name, test_func in tests:
            print(f"\n{'-'*60}")
            print(f"📝 {test_name}")
            print(f"{'-'*60}")
            
            start_time = datetime.now()
            try:
                result = await test_func()
                duration = (datetime.now() - start_time).total_seconds()
                
                # Подсчитываем успешные тесты
                lines = result.split('\n')
                total_lines = len(lines)
                success_lines = sum(1 for line in lines if line.startswith('✅'))
                
                overall_results.append({
                    'name': test_name,
                    'total': total_lines,
                    'passed': success_lines,
                    'duration': duration
                })
                
                print(result)
                print(f"\n📊 {test_name}: {success_lines}/{total_lines} ({success_lines/total_lines*100:.1f}%) за {duration:.1f}с")
                
            except Exception as e:
                print(f"❌ ОШИБКА В ТЕСТЕ: {e}")
                overall_results.append({
                    'name': test_name,
                    'total': 1,
                    'passed': 0,
                    'duration': (datetime.now() - start_time).total_seconds()
                })

        # Итоговый отчет
        print("\n" + "="*80)
        print("📊 ИТОГОВЫЙ ОТЧЕТ")
        print("="*80)
        
        total_tests = sum(r['total'] for r in overall_results)
        total_passed = sum(r['passed'] for r in overall_results)
        total_time = sum(r['duration'] for r in overall_results)
        
        print(f"Всего тестов: {total_tests}")
        print(f"✅ Пройдено: {total_passed}")
        print(f"❌ Провалено: {total_tests - total_passed}")
        print(f"📈 Успешность: {total_passed/total_tests*100:.1f}%")
        print(f"⏱️ Общее время: {total_time:.1f}с")
        
        print("\nДетали по категориям:")
        for result in overall_results:
            success_rate = result['passed']/result['total']*100 if result['total'] > 0 else 0
            status = "✅" if success_rate >= 80 else "⚠️" if success_rate >= 50 else "❌"
            print(f"  {status} {result['name']}: {result['passed']}/{result['total']} ({success_rate:.1f}%) за {result['duration']:.1f}с")
        
        print("\n" + "="*80)
        if total_passed/total_tests >= 0.8:
            print("🎉 ОТЛИЧНЫЙ РЕЗУЛЬТАТ! AI агент работает стабильно!")
        elif total_passed/total_tests >= 0.6:
            print("👍 ХОРОШИЙ РЕЗУЛЬТАТ! Есть небольшие проблемы.")
        else:
            print("⚠️ ТРЕБУЮТСЯ УЛУЧШЕНИЯ. Много ошибок.")

async def main():
    """Основная функция"""
    test_suite = QuickAITestSuite()
    try:
        await test_suite.run_all_tests()
    except KeyboardInterrupt:
        print("\n🛑 Тестирование прервано пользователем")
    except Exception as e:
        print(f"\n💥 КРИТИЧЕСКАЯ ОШИБКА: {e}")

if __name__ == "__main__":
    asyncio.run(main())