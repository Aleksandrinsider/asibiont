#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_integration.utils import get_filtered_news_for_user

def test_news_filter():
    """Тест функции get_filtered_news_for_user"""
    try:
        # Тест с несуществующим пользователем
        result = get_filtered_news_for_user(999999)
        print(f"Test 1 - Non-existent user: {result}")

        # Тест с существующим пользователем (если есть)
        # result = get_filtered_news_for_user(123456789)  # Замените на реальный ID
        # print(f"Test 2 - Existing user: {result[:100] if result else 'None'}...")

        print("Test completed successfully!")
        return True

    except Exception as e:
        print(f"Test failed: {e}")
        return False

if __name__ == "__main__":
    test_news_filter()