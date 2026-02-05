#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Простой тест фильтрации новостей без импорта через __init__.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Импортируем напрямую из utils
from ai_integration.utils import get_filtered_news_for_user

def test_news_filtering():
    """Тестируем фильтрацию новостей"""

    print("=== Тест фильтрации новостей ===\n")

    # Тест 1: Пользователь с интересами в политике
    user_interests_1 = ["политика", "экономика", "спорт"]
    result_1 = get_filtered_news_for_user(user_interests_1, subscription_tier="PREMIUM")
    print(f"Интересы: {user_interests_1}")
    print(f"Результат: {result_1}\n")

    # Тест 2: Пользователь с интересами в технологиях
    user_interests_2 = ["программирование", "искусственный интеллект", "гаджеты"]
    result_2 = get_filtered_news_for_user(user_interests_2, subscription_tier="FREE")
    print(f"Интересы: {user_interests_2}")
    print(f"Результат: {result_2}\n")

    # Тест 3: Пользователь без интересов
    user_interests_3 = []
    result_3 = get_filtered_news_for_user(user_interests_3)
    print(f"Интересы: {user_interests_3}")
    print(f"Результат: {result_3}\n")

    # Тест 4: Пользователь с нерелевантными интересами
    user_interests_4 = ["путешествия", "кулинария", "фотография"]
    result_4 = get_filtered_news_for_user(user_interests_4, subscription_tier="PREMIUM")
    print(f"Интересы: {user_interests_4}")
    print(f"Результат: {result_4}\n")

if __name__ == "__main__":
    test_news_filtering()