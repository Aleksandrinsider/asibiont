#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Комплексный тест реального выполнения функций агентом
Проверяет не только tool_calls, но и фактические результаты
"""

import asyncio
import sys
import os
sys.path.append('.')

from ai_integration.autonomous_agent import chat_with_ai
from models import User, Task, SessionLocal
from datetime import datetime

async def comprehensive_function_test():
    print("🧪 КОМПЛЕКСНЫЙ ТЕСТ РЕАЛЬНОГО ВЫПОЛНЕНИЯ ФУНКЦИЙ\n")

    # Создаем тестового пользователя
    session = SessionLocal()
    try:
        # Очищаем старые тестовые данные
        session.query(Task).filter_by(user_id=1).delete()
        session.commit()

        user = User(id=1, telegram_id=123456789, username='test_user', subscription_tier='STANDARD', created_at='2024-01-01')

        test_scenarios = [
            {
                'query': 'Привет!',
                'expected_functions': ['list_tasks'],
                'check_db': True,
                'description': 'Приветствие - должен вызвать list_tasks'
            },
            {
                'query': 'Как приготовить пасту карбонара?',
                'expected_functions': ['research_topic'],
                'check_db': False,
                'description': 'Кулинария - должен вызвать research_topic'
            },
            {
                'query': 'Где найти единомышленников по AI?',
                'expected_functions': ['find_partners'],
                'check_db': False,
                'description': 'Нетворкинг - должен вызвать find_partners'
            },
            {
                'query': 'Создай задачу: изучить Python асинхронность',
                'expected_functions': ['add_task'],
                'check_db': True,
                'description': 'Создание задачи - должен вызвать add_task'
            },
            {
                'query': 'Сделал задачу про Python',
                'expected_functions': ['complete_task'],
                'check_db': True,
                'description': 'Завершение задачи - должен вызвать complete_task'
            }
        ]

        total_tests = len(test_scenarios)
        successful_tests = 0

        for i, scenario in enumerate(test_scenarios, 1):
            print(f"🧪 Тест {i}/{total_tests}: {scenario['description']}")
            print(f"   ❓ {scenario['query']}")

            # Запоминаем состояние БД до запроса
            initial_tasks = []
            if scenario['check_db']:
                initial_tasks = session.query(Task).filter_by(user_id=user.id).all()
                print(f"   📊 Задач до: {len(initial_tasks)}")

            try:
                # Выполняем запрос
                result = await chat_with_ai(
                    message=scenario['query'],
                    user_id=user.id,
                    db_session=session
                )

                response = result['response']
                tool_calls = result.get('tool_calls', [])
                used_tools = [call.get('function', {}).get('name', '') for call in tool_calls]

                print(f"   📝 Ответ: {len(response)} символов")
                print(f"   🔧 Вызванные инструменты: {used_tools if used_tools else 'нет'}")

                # Проверяем tool_calls
                functions_called = any(func in used_tools for func in scenario['expected_functions'])

                # Проверяем изменения в БД
                db_changed = False
                if scenario['check_db']:
                    final_tasks = session.query(Task).filter_by(user_id=user.id).all()
                    print(f"   📊 Задач после: {len(final_tasks)}")

                    if len(final_tasks) != len(initial_tasks):
                        db_changed = True
                        print("   ✅ БД изменилась")

                        # Показываем детали изменений
                        if 'add_task' in scenario['expected_functions']:
                            new_tasks = [t for t in final_tasks if t not in initial_tasks]
                            if new_tasks:
                                print(f"   ➕ Создана задача: '{new_tasks[0].title}'")
                        elif 'complete_task' in scenario['expected_functions']:
                            completed_tasks = [t for t in final_tasks if t.status == 'completed' and t not in [it for it in initial_tasks if it.status == 'completed']]
                            if completed_tasks:
                                print(f"   ✅ Завершена задача: '{completed_tasks[0].title}'")

                # Определяем успех
                if scenario['check_db']:
                    success = functions_called and db_changed
                else:
                    success = functions_called

                if success:
                    print("   ✅ ФУНКЦИЯ РЕАЛЬНО ВЫПОЛНЕНА!")
                    successful_tests += 1
                else:
                    issues = []
                    if not functions_called:
                        issues.append("функция не вызвана")
                    if scenario['check_db'] and not db_changed:
                        issues.append("БД не изменилась")
                    print(f"   ❌ ПРОБЛЕМЫ: {', '.join(issues)}")

                # Показываем начало ответа
                preview = response[:150].replace('\n', ' ')
                print(f"   💬 Ответ: {preview}...")

            except Exception as e:
                print(f"   ✗ ОШИБКА: {e}")
                import traceback
                traceback.print_exc()

            print()

        # Финальные результаты
        success_rate = (successful_tests / total_tests) * 100
        print(f"🎯 ИТОГОВЫЙ РЕЗУЛЬТАТ: {successful_tests}/{total_tests} ({success_rate:.1f}%)")

        if success_rate == 100:
            print("🏆 ОТЛИЧНО! Все функции выполняются реально!")
        elif success_rate >= 80:
            print("👍 ОЧЕНЬ ХОРОШО! Большинство функций работает корректно.")
        elif success_rate >= 60:
            print("👌 ХОРОШО! Большая часть функций функционирует.")
        else:
            print("🔧 НУЖНО ДОРАБОТАТЬ выполнение функций.")

    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(comprehensive_function_test())