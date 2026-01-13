#!/usr/bin/env python3
"""
Тест оптимизаций ИИ для сокращения расходов и улучшения производительности
"""

import time
import sys
import os

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration import chat_with_ai
from improved_prompts_final import improved_classify_intent

def test_ai_caching():
    """Test AI response caching - DISABLED"""
    print("=== Кеширование ИИ отключено ===")
    print("Кеширование ответов ИИ отключено для обеспечения живого диалога")

def test_intent_caching():
    """Test intent classification (no caching)"""
    print("\n=== Тестирование классификации намерений (без кеширования) ===")

    test_message = "Добавь задачу купить молоко завтра"

    # First call
    start_time = time.time()
    result1 = improved_classify_intent(test_message)
    first_call_time = time.time() - start_time

    # Second call (no caching)
    start_time = time.time()
    result2 = improved_classify_intent(test_message)
    second_call_time = time.time() - start_time

    print(".2f")
    print(".2f")
    print(".2f")
    print(f"Intent type: {result1}")

def test_model_update_readiness():
    """Test readiness for model update"""
    print("\n=== Проверка готовности к обновлению модели ===")

    from config import DEEPSEEK_MODEL, AI_CACHE_ENABLED, AI_MAX_TOKENS_RESPONSE

    print(f"Current model: {DEEPSEEK_MODEL}")
    print(f"AI Cache enabled: {AI_CACHE_ENABLED}")
    print(f"Max tokens for responses: {AI_MAX_TOKENS_RESPONSE}")

    # Test environment variable override
    os.environ['DEEPSEEK_MODEL'] = 'deepseek-v3.2'
    os.environ['AI_MAX_TOKENS_RESPONSE'] = '800'

    # Reload config would be needed in real scenario
    print("Environment variables set for testing:")
    print(f"DEEPSEEK_MODEL={os.environ.get('DEEPSEEK_MODEL')}")
    print(f"AI_MAX_TOKENS_RESPONSE={os.environ.get('AI_MAX_TOKENS_RESPONSE')}")

if __name__ == "__main__":
    print("🚀 Тестирование оптимизаций ИИ\n")

    test_ai_caching()
    test_intent_caching()
    test_model_update_readiness()

    print("\n✅ Все тесты оптимизаций завершены!")