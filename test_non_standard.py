#!/usr/bin/env python3
"""
Тест нестандартных ответов пользователя для гибридного подхода
"""
import sys
import os
import asyncio
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Устанавливаем локальный режим
os.environ['LOCAL'] = '1'

# Инициализируем базу данных
from init_db import *

from ai_integration.chat import chat_with_ai

async def test_non_standard_responses():
    user_id = 999999999

    print("=== ТЕСТ НЕСТАНДАРТНЫХ ОТВЕТОВ ПОЛЬЗОВАТЕЛЯ ===\n")

    # Тесты завершения задач (только первые 5)
    print("🔄 ТЕСТЫ ЗАВЕРШЕНИЯ ЗАДАЧ:")
    completion_tests = [
        "я только с пробежки",
        "только что закончил",
        "готово!",
        "сделал",
        "завершил задачу",
    ]

    for i, message in enumerate(completion_tests, 1):
        print(f"{i}. Сообщение: '{message}'")
        try:
            response = await chat_with_ai(message, user_id)
            print(f"   Ответ: {response[:100]}{'...' if len(response) > 100 else ''}")
        except Exception as e:
            print(f"   Ошибка: {e}")
        print()

    # Тесты создания задач (только первые 5)
    print("➕ ТЕСТЫ СОЗДАНИЯ ЗАДАЧ:")
    add_task_tests = [
        "создай задачу сделать отчет",
        "добавь: позвонить маме",
        "напомни купить молоко",
        "нужно сделать уборку",
        "задача: подготовить презентацию",
    ]

    for i, message in enumerate(add_task_tests, 1):
        print(f"{i}. Сообщение: '{message}'")
        try:
            response = await chat_with_ai(message, user_id)
            print(f"   Ответ: {response[:100]}{'...' if len(response) > 100 else ''}")
        except Exception as e:
            print(f"   Ошибка: {e}")
        print()

    # Тесты делегирования задач (только первые 5)
    print("👥 ТЕСТЫ ДЕЛЕГИРОВАНИЯ ЗАДАЧ:")
    delegation_tests = [
        "поручи @test1 сделать отчет",
        "передай @user задачу о звонке",
        "делегируй @test1 подготовить презентацию",
        "@test2 сделай уборку",
        "поручи @user1 написать письмо",
    ]

    for i, message in enumerate(delegation_tests, 1):
        print(f"{i}. Сообщение: '{message}'")
        try:
            response = await chat_with_ai(message, user_id)
            print(f"   Ответ: {response[:100]}{'...' if len(response) > 100 else ''}")
        except Exception as e:
            print(f"   Ошибка: {e}")
        print()

    # Тесты смешанных сценариев (только первые 3)
    print("🔀 ТЕСТЫ СМЕШАННЫХ СЦЕНАРИЕВ:")
    mixed_tests = [
        "сделал отчет, теперь поручи @test1 проверить его",
        "только что закончил, добавь задачу на завтра",
        "готово! @user2 теперь твоя очередь сделать анализ",
    ]

    for i, message in enumerate(mixed_tests, 1):
        print(f"{i}. Сообщение: '{message}'")
        try:
            response = await chat_with_ai(message, user_id)
            print(f"   Ответ: {response[:100]}{'...' if len(response) > 100 else ''}")
        except Exception as e:
            print(f"   Ошибка: {e}")
        print()

if __name__ == "__main__":
    asyncio.run(test_non_standard_responses())