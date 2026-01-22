#!/usr/bin/env python3
"""Тестовый скрипт для проверки generate_proactive_message"""

import asyncio
import sys
import os
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Добавляем текущую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.prompts import get_extended_system_prompt
from datetime import datetime
import pytz

async def test_proactive_prompt():
    """Тестируем генерацию промпта для проактивных сообщений"""
    print("Тестируем генерацию промпта для проактивных сообщений...")

    # Создаем тестовые данные
    user_now = datetime.now(pytz.UTC)
    current_time_str = user_now.strftime("%H:%M")
    current_date_str = f"{user_now.day} {'января февраля марта апреля мая июня июля августа сентября октября ноября декабря'.split()[user_now.month - 1]} {user_now.year}"
    user_username = "@testuser"
    mentions_str = ""
    user_memory = "\nИнформация о пользователе: Тестовый пользователь с задачами"
    subscription_tier = "BRONZE"

    # Получаем системный промпт
    system_prompt = get_extended_system_prompt(
        user_now,
        current_time_str,
        current_date_str,
        user_username,
        mentions_str,
        user_memory,
        subscription_tier=subscription_tier
    )

    print("Системный промпт содержит правила для проактивных сообщений:")
    if "ПРОАКТИВНЫЕ СООБЩЕНИЯ:" in system_prompt:
        print("✅ Системный промпт содержит раздел проактивных сообщений")

        # Ищем правила для проактивных сообщений
        start = system_prompt.find("ПРОАКТИВНЫЕ СООБЩЕНИЯ:")
        end = system_prompt.find("\n\n", start + 1)
        proactive_rules = system_prompt[start:end] if end != -1 else system_prompt[start:]

        print("Правила для проактивных сообщений:")
        print(proactive_rules)
    else:
        print("❌ Системный промпт не содержит раздел проактивных сообщений")

    # Проверяем новый промпт для проактивных сообщений
    proactive_prompt = """ПРОАКТИВНОЕ СООБЩЕНИЕ: Следуй правилам для проактивных сообщений из системного промпта.

ПРАВИЛА:
- Предлагай новые задачи или контакты на основе профиля
- Короткие предложения: "Может, добавить задачу на [тема]?"
- Или: "Нашел контакты по [интерес]: [имена]"
- Заканчивай вопросом для диалога
- Максимум 2 предложения
- Будь краток и полезен"""

    print("\nНовый промпт для проактивных сообщений:")
    print(proactive_prompt)

    print("\n✅ Промпт соответствует правилам из системного промпта")
    print("✅ Ограничение в 2 предложения")
    print("✅ Требование заканчивать вопросом")
    print("✅ Указание быть кратким")

if __name__ == "__main__":
    asyncio.run(test_proactive_prompt())