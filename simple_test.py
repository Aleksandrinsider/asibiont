#!/usr/bin/env python3
"""
Простой тест сбора данных
"""

import os
os.environ["FREE_ACCESS_MODE"] = "1"

import asyncio
import sys
sys.path.append('.')

from ai_integration.chat import chat_with_ai

async def test():
    print("🧪 ТЕСТ СБОРА ДАННЫХ AI-АГЕНТОМ")
    print("=" * 50)

    # Тест с приветствием
    print("\n🔹 ТЕСТ: Приветствие")
    result = await chat_with_ai("Привет!", user_id=999000333999)
    response = result.get('response', '')
    print(f"Ответ: {response[:200]}...")

    has_question = any(phrase in response.lower() for phrase in [
        'город', 'интересы', 'навыки', 'цели', 'компания', 'должность',
        'чем занимаешься', 'расскажи о себе', 'где живешь'
    ])
    print(f"✅ Задает вопросы о профиле: {'ДА' if has_question else 'НЕТ'}")

    # Тест с вопросом о возможностях
    print("\n🔹 ТЕСТ: Вопрос о возможностях")
    result = await chat_with_ai("Что ты умеешь?", user_id=999000333999)
    response = result.get('response', '')
    print(f"Ответ: {response[:200]}...")

    has_goals_question = any(phrase in response.lower() for phrase in [
        'цели', 'приоритеты', 'хочешь достичь', 'планируешь', 'работаешь над'
    ])
    print(f"✅ Спрашивает о целях: {'ДА' if has_goals_question else 'НЕТ'}")

    print("\n" + "=" * 50)
    success = has_question or has_goals_question
    print(f"🎯 РЕЗУЛЬТАТ: {'AI-агент АКТИВНО собирает данные!' if success else '⚠️ AI-агент НЕ задает вопросы'}")

if __name__ == "__main__":
    asyncio.run(test())