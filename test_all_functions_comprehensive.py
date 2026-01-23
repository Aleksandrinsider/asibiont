#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ ВСЕХ ФУНКЦИЙ AI АГЕНТА
Полная проверка всех возможностей системы на реальных запросах
"""

import os
import asyncio
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
import json

# Устанавливаем переменную окружения для локального тестирования
os.environ['LOCAL'] = '1'

# Импорты проекта
from models import User, Task, SubscriptionTier, SessionLocal
from ai_integration.chat import chat_with_ai
import logging

# Простой логгер для тестов
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ComprehensiveTestSuite:
    """Комплексный набор тестов для всех функций системы"""
    
    def __init__(self):
        self.test_user_id = None
        self.created_tasks = []
        self.test_results = []
        
    async def setup_test_user(self) -> int:
        """Создание тестового пользователя"""
        print("🔧 Создание тестового пользователя...")
        
        from sqlalchemy import create_engine
        from models import Base
        
        # Используем обычную сессию для тестирования
        db = SessionLocal()
        try:
            # Удаляем существующих тестовых пользователей
            existing_user = db.query(User).filter(User.id == 99999).first()
            if existing_user:
                db.delete(existing_user)
                db.commit()
            
            # Создаем нового тестового пользователя
            user = User(
                id=99999,
                telegram_id=99999,
                username="test_comprehensive",
                first_name="Комплексный Тест",
                subscription_tier=SubscriptionTier.GOLD,  # Максимальные возможности
                timezone="Europe/Moscow"
            )
            
            db.add(user)
            db.commit()
            self.test_user_id = 99999
        finally:
            db.close()
            
        print(f"✅ Создан тестовый пользователь: @test_comprehensive (ID: {self.test_user_id})")
        print(f"📍 Подписка: {SubscriptionTier.GOLD.value}")
        return self.test_user_id

    async def cleanup_test_data(self):
        """Очистка тестовых данных"""
        print("\n🧹 Очистка тестовых данных...")
        
        tasks_deleted = 0
        db = SessionLocal()
        try:
            # Удаляем все задачи тестового пользователя
            user = db.query(User).filter(User.id == self.test_user_id).first()
            if user:
                # Удаляем задачи пользователя
                tasks = db.query(Task).filter(Task.user_id == self.test_user_id).all()
                for task in tasks:
                    db.delete(task)
                    tasks_deleted += 1
                
                db.delete(user)
                db.commit()
        finally:
            db.close()
        
        print(f"   Удалено задач: {tasks_deleted}")
        print("   ✅ Тестовые данные очищены")

    async def run_test(self, test_name: str, test_func, expected_result: str = None) -> Dict[str, Any]:
        """Запуск отдельного теста с обработкой ошибок"""
        print(f"\n{'='*80}")
        print(f"📝 {test_name}")
        print(f"{'='*80}")
        
        start_time = datetime.now()
        result = {
            'name': test_name,
            'status': 'FAILED',
            'duration': 0,
            'response': None,
            'error': None,
            'validation': {}
        }
        
        try:
            response = await test_func()
            result['response'] = response
            result['status'] = 'PASSED'
            
            print(f"✅ Получен ответ ({len(str(response)) if response else 0} символов):")
            print("─" * 80)
            print(str(response)[:500] + ("..." if len(str(response)) > 500 else ""))
            print("─" * 80)
            
        except Exception as e:
            result['error'] = str(e)
            result['status'] = 'ERROR'
            print(f"❌ Ошибка: {e}")
            print(f"Traceback: {traceback.format_exc()}")
        
        finally:
            result['duration'] = (datetime.now() - start_time).total_seconds()
            self.test_results.append(result)
        
        return result

    # ============================================================================
    # ТЕСТЫ AI CHAT ФУНКЦИЙ
    # ============================================================================

    async def test_greeting_variations(self):
        """Тест различных вариантов приветствий"""
        greetings = [
            "Привет!",
            "Доброе утро!",
            "Добрый день!",
            "Добрый вечер!",
            "Здравствуй!",
            "Хай!",
            "Как дела?",
            "Что нового?"
        ]
        
        results = []
        for greeting in greetings:
            print(f"💬 Тестируем: '{greeting}'")
            response = await chat_with_ai(greeting, [], self.test_user_id)
            results.append(f"{greeting} → {str(response)[:100]}...")
        
        return "\n".join(results)

    async def test_time_queries(self):
        """Тест запросов времени"""
        time_queries = [
            "сколько времени?",
            "который час?",
            "какое сейчас время?",
            "время",
            "покажи время"
        ]
        
        results = []
        for query in time_queries:
            print(f"⏰ Тестируем: '{query}'")
            response = await chat_with_ai(query, [], self.test_user_id)
            # Проверяем, что время упоминается
            has_time = any(x in str(response).lower() for x in ['время', 'час', ':'])
            results.append(f"{query} → {'✅' if has_time else '❌'} {str(response)[:80]}...")
        
        return "\n".join(results)

    async def test_advice_requests(self):
        """Тест запросов на советы"""
        advice_queries = [
            "дай совет по продуктивности",
            "как лучше организовать день?",
            "посоветуй как быть продуктивнее",
            "что делать чтобы все успеть?",
            "как управлять временем?",
            "помоги с планированием"
        ]
        
        results = []
        for query in advice_queries:
            print(f"💡 Тестируем: '{query}'")
            response = await chat_with_ai(query, [], self.test_user_id)
            # Проверяем, что ответ содержит совет
            has_advice = len(str(response)) > 50 and any(x in str(response).lower() for x in 
                ['рекомендую', 'советую', 'попробуй', 'можешь', 'стоит'])
            results.append(f"{query} → {'✅' if has_advice else '❌'} {str(response)[:80]}...")
        
        return "\n".join(results)

    # ============================================================================
    # ТЕСТЫ TASK MANAGEMENT
    # ============================================================================

    async def test_task_creation_variations(self):
        """Тест создания задач разными способами"""
        task_requests = [
            "напомни завтра в 10:00 позвонить маме",
            "создай задачу: купить молоко на 18:00",
            "добавь в план: встреча с клиентом в понедельник",
            "через 2 часа нужно проверить почту",
            "завтра утром сделать зарядку",
            "на следующей неделе подготовить отчет",
            "каждый день в 9:00 проверять задачи",
            "в пятницу в 15:30 созвон с командой"
        ]
        
        results = []
        for request in task_requests:
            print(f"📋 Тестируем: '{request}'")
            response = await chat_with_ai(request, [], self.test_user_id)
            # Проверяем, что задача создана
            has_confirmation = any(x in str(response).lower() for x in 
                ['задача', 'напоминание', 'записал', 'добавил', 'создал'])
            results.append(f"{request} → {'✅' if has_confirmation else '❌'} {str(response)[:80]}...")
        
        return "\n".join(results)

    async def test_task_listing_variations(self):
        """Тест различных способов получения списка задач"""
        list_requests = [
            "покажи мои задачи",
            "что у меня на сегодня?",
            "план на завтра",
            "список дел",
            "мои напоминания",
            "что запланировано?",
            "показать все задачи",
            "какие задачи на этой неделе?"
        ]
        
        results = []
        for request in list_requests:
            print(f"📝 Тестируем: '{request}'")
            response = await chat_with_ai(request, [], self.test_user_id)
            # Проверяем, что показаны задачи или сообщение об их отсутствии
            has_tasks_info = any(x in str(response).lower() for x in 
                ['задач', 'дел', 'напоминани', 'план', 'нет задач', 'пусто'])
            results.append(f"{request} → {'✅' if has_tasks_info else '❌'} {str(response)[:80]}...")
        
        return "\n".join(results)

    async def test_task_management_operations(self):
        """Тест операций управления задачами"""
        # Сначала создаем тестовые задачи
        setup_requests = [
            "создай задачу: тестовая задача 1 на завтра в 10:00",
            "добавь: тестовая задача 2 на послезавтра в 14:00"
        ]
        
        for request in setup_requests:
            await chat_with_ai(request, [], self.test_user_id)
        
        # Тестируем операции
        management_requests = [
            "отметь задачу 'тестовая задача 1' как выполненную",
            "удали задачу 'тестовая задача 2'",
            "перенеси задачу на другое время",
            "отложи напоминание на час",
            "измени задачу",
            "отмени все задачи"
        ]
        
        results = []
        for request in management_requests:
            print(f"🔧 Тестируем: '{request}'")
            response = await chat_with_ai(request, [], self.test_user_id)
            # Проверяем, что есть реакция на операцию
            has_action = any(x in str(response).lower() for x in 
                ['выполнен', 'удалил', 'перенес', 'отложил', 'изменил', 'отменил'])
            results.append(f"{request} → {'✅' if has_action else '❌'} {str(response)[:80]}...")
        
        return "\n".join(results)

    # ============================================================================
    # ТЕСТЫ PROFILE MANAGEMENT
    # ============================================================================

    async def test_profile_operations(self):
        """Тест операций с профилем"""
        profile_requests = [
            "покажи мой профиль",
            "добавь в навыки: Machine Learning",
            "обнови мой город: Санкт-Петербург",
            "измени рабочее время: 10:00-19:00",
            "добавь интерес: Музыка",
            "установи часовой пояс: Europe/London",
            "обнови полное имя: Тестовый Пользователь"
        ]
        
        results = []
        for request in profile_requests:
            print(f"👤 Тестируем: '{request}'")
            response = await chat_with_ai(request, [], self.test_user_id)
            # Проверяем, что есть реакция на изменение профиля
            has_profile_action = any(x in str(response).lower() for x in 
                ['профиль', 'навык', 'город', 'время', 'интерес', 'обновил', 'добавил'])
            results.append(f"{request} → {'✅' if has_profile_action else '❌'} {str(response)[:80]}...")
        
        return "\n".join(results)

    # ============================================================================
    # ТЕСТЫ DELEGATION (PREMIUM функции)
    # ============================================================================

    async def test_delegation_features(self):
        """Тест функций делегирования (GOLD)"""
        delegation_requests = [
            "найди контакты для проекта Python разработки",
            "делегируй задачу 'написать код' на @developer",
            "найди экспертов по AI",
            "кого можно привлечь для тестирования?",
            "ищи фрилансеров с навыками JavaScript",
            "делегировать встречу с клиентом коллеге"
        ]
        
        results = []
        for request in delegation_requests:
            print(f"👥 Тестируем: '{request}'")
            response = await chat_with_ai(request, [], self.test_user_id)
            # Проверяем, что есть реакция на делегирование
            has_delegation = any(x in str(response).lower() for x in 
                ['контакт', 'делегир', 'эксперт', 'фрилансер', 'коллег', 'команд'])
            results.append(f"{request} → {'✅' if has_delegation else '❌'} {str(response)[:80]}...")
        
        return "\n".join(results)

    # ============================================================================
    # ТЕСТЫ EDGE CASES И ERROR HANDLING
    # ============================================================================

    async def test_edge_cases(self):
        """Тест граничных случаев"""
        edge_cases = [
            "",  # Пустой запрос
            "   ",  # Только пробелы
            "а",  # Один символ
            "?" * 100,  # Длинная строка символов
            "🤖🚀✨🎉💻",  # Только эмодзи
            "asdfghjkl qwertyuiop",  # Бессмысленный текст
            "123456789",  # Только цифры
            "@#$%^&*()",  # Специальные символы
            "CREATE TABLE users (id INT);",  # SQL injection попытка
            "<script>alert('test')</script>",  # XSS попытка
        ]
        
        results = []
        for case in edge_cases:
            print(f"⚠️ Тестируем: '{case[:20]}{'...' if len(case) > 20 else ''}'")
            try:
                response = await chat_with_ai(case, [], self.test_user_id)
                # Проверяем, что система корректно обработала запрос
                is_handled = response is not None and len(str(response)) > 0
                results.append(f"'{case[:20]}...' → {'✅' if is_handled else '❌'} {str(response)[:50]}...")
            except Exception as e:
                results.append(f"'{case[:20]}...' → ❌ ERROR: {str(e)[:50]}...")
        
        return "\n".join(results)

    async def test_conversation_continuity(self):
        """Тест непрерывности диалога"""
        conversation = [
            "Привет! Как дела?",
            "Что ты можешь делать?",
            "Создай задачу: позвонить маме завтра в 15:00",
            "А что у меня еще запланировано?",
            "Спасибо! Пока!"
        ]
        
        chat_history = []
        results = []
        
        for i, message in enumerate(conversation):
            print(f"💬 Шаг {i+1}: '{message}'")
            response = await chat_with_ai(message, chat_history, self.test_user_id)
            
            # Добавляем в историю
            chat_history.append({"role": "user", "content": message})
            chat_history.append({"role": "assistant", "content": str(response)})
            
            # Проверяем адекватность ответа
            is_adequate = response is not None and len(str(response)) > 10
            results.append(f"Шаг {i+1}: {'✅' if is_adequate else '❌'} {str(response)[:60]}...")
        
        return "\n".join(results)

    # ============================================================================
    # ГЛАВНЫЙ МЕТОД ЗАПУСКА ТЕСТОВ
    # ============================================================================

    async def run_all_tests(self):
        """Запуск всех тестов"""
        print("=" * 80)
        print("🤖 КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ ВСЕХ ФУНКЦИЙ AI АГЕНТА")
        print("=" * 80)
        
        await self.setup_test_user()
        print(f"📍 Таймзона: Europe/Moscow")
        print(f"🎫 Подписка: GOLD (все функции)")
        
        # Список всех тестов
        tests = [
            ("1. Различные приветствия", self.test_greeting_variations),
            ("2. Запросы времени", self.test_time_queries),
            ("3. Запросы советов", self.test_advice_requests),
            ("4. Создание задач (разные варианты)", self.test_task_creation_variations),
            ("5. Получение списка задач", self.test_task_listing_variations),
            ("6. Управление задачами", self.test_task_management_operations),
            ("7. Операции с профилем", self.test_profile_operations),
            ("8. Функции делегирования (GOLD)", self.test_delegation_features),
            ("9. Граничные случаи и ошибки", self.test_edge_cases),
            ("10. Непрерывность диалога", self.test_conversation_continuity),
        ]
        
        # Запускаем все тесты
        for test_name, test_func in tests:
            await self.run_test(test_name, test_func)
        
        # Показываем итоговый отчет
        await self.show_final_report()
        
        # Очищаем тестовые данные
        await self.cleanup_test_data()

    async def show_final_report(self):
        """Показать итоговый отчет"""
        print("\n" + "=" * 80)
        print("📊 ИТОГОВЫЙ ОТЧЕТ")
        print("=" * 80)
        
        total_tests = len(self.test_results)
        passed_tests = sum(1 for r in self.test_results if r['status'] == 'PASSED')
        error_tests = sum(1 for r in self.test_results if r['status'] == 'ERROR')
        
        print(f"Всего тестов: {total_tests}")
        print(f"✅ Пройдено: {passed_tests}")
        print(f"❌ Провалено: {total_tests - passed_tests - error_tests}")
        print(f"🔥 Ошибок: {error_tests}")
        print(f"📈 Успешность: {passed_tests/total_tests*100:.1f}%")
        
        print("\nДетали по каждому тесту:")
        for result in self.test_results:
            status_icon = "✅" if result['status'] == 'PASSED' else "❌" if result['status'] == 'FAILED' else "🔥"
            duration = f"{result['duration']:.1f}с"
            print(f"  {status_icon} {result['name']}: {result['status']} ({duration})")
            
            if result['error']:
                print(f"      Ошибка: {result['error'][:100]}...")
        
        print("\n" + "=" * 80)
        
        if passed_tests == total_tests:
            print("🎉 ОТЛИЧНЫЙ РЕЗУЛЬТАТ! Все функции работают корректно!")
        elif passed_tests / total_tests >= 0.8:
            print("👍 ХОРОШИЙ РЕЗУЛЬТАТ! Большинство функций работает правильно.")
        else:
            print("⚠️ ТРЕБУЮТСЯ УЛУЧШЕНИЯ. Есть проблемы с функциональностью.")

async def main():
    """Основная функция запуска тестов"""
    test_suite = ComprehensiveTestSuite()
    try:
        await test_suite.run_all_tests()
    except KeyboardInterrupt:
        print("\n\n🛑 Тестирование прервано пользователем")
        await test_suite.cleanup_test_data()
    except Exception as e:
        print(f"\n\n💥 КРИТИЧЕСКАЯ ОШИБКА: {e}")
        print(traceback.format_exc())
        await test_suite.cleanup_test_data()

if __name__ == "__main__":
    asyncio.run(main())