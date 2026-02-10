#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import os
import sys

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import UserProfile

async def test_no_tasks_response():
    """
    Тест: как агент отвечает когда у пользователя нет задач
    Проверяем что он стал полезнее и конкретнее
    """
    
    print("🎯 ТЕСТ: Реакция агента на 'нет задач'")
    print("=" * 60)
    
    # Используем существующего тестового пользователя
    test_user_id = 99999  # id из других тестов
    
    # Тестовый сценарий
    message = "пока задач нет"
    
    print(f"👤 Пользователь: {message}")
    print(f"💼 Пользователь ID: {test_user_id}")
    print()
    
    try:
        # Вызываем агента
        response = await chat_with_ai(
            message=message,
            user_id=test_user_id
        )
        
        # Анализируем ответ
        if isinstance(response, dict):
            response_text = response.get('response', 'Нет ответа')
            tools_used = response.get('tools_used', [])
        else:
            response_text = str(response)
            tools_used = []
        
        print("🤖 Ответ агента:")
        print("-" * 40)
        print(response_text)
        print("-" * 40)
        print()
        
        # Анализ качества
        lines = response_text.split('\n')
        actual_lines = [line.strip() for line in lines if line.strip()]
        
        print("📊 Анализ качества:")
        print(f"   📏 Количество строк: {len(actual_lines)}")
        
        # Проверка на запрещенные фразы
        bad_phrases = ["отлично", "круто", "понимаю", "слушаю тебя", "какой вариант", "что больше подходит"]
        found_bad = []
        for phrase in bad_phrases:
            if phrase.lower() in response_text.lower():
                found_bad.append(phrase)
        
        if found_bad:
            print(f"   ❌ Найдены запрещенные фразы: {found_bad}")
        else:
            print("   ✅ Нет запрещенных фраз")
        
        # Проверка на полезность
        useful_actions = ["найти", "создать", "проверить", "показать", "партнер", "цель"]
        found_actions = []
        for action in useful_actions:
            if action.lower() in response_text.lower():
                found_actions.append(action)
        
        if found_actions:
            print(f"   ✅ Полезные действия: {found_actions}")
        else:
            print("   ❌ Нет конкретных полезных действий")
        
        # Проверка инструментов
        if tools_used:
            print(f"   🛠️ Использованы инструменты: {tools_used}")
        else:
            print("   ⚠️ Инструменты не использованы")
        
        # Оценка краткости
        if len(actual_lines) <= 3:
            print("   ✅ Ответ краткий (≤3 строк)")
        elif len(actual_lines) <= 5:
            print("   ⚠️ Ответ средний (4-5 строк)")
        else:
            print("   ❌ Ответ слишком длинный (>5 строк)")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    asyncio.run(test_no_tasks_response())