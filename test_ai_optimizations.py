#!/usr/bin/env python3
"""
Тест оптимизаций ИИ для сокращения расходов и улучшения производительности
"""

import time
import sys
import os

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration import get_ai_cache_key, get_cached_ai_response, cache_ai_response, classify_intent_cached
from improved_prompts_final import improved_classify_intent

def test_ai_caching():
    """Test AI response caching"""
    print("=== Тестирование кеширования ИИ ===")

    # Test cache key generation
    prompt = "Test prompt"
    model = "deepseek-chat"
    temp = 0.1
    tokens = 150

    cache_key = get_ai_cache_key(prompt, model, temp, tokens)
    print(f"Cache key generated: {cache_key[:16]}...")

    # Test caching
    test_response = "Test cached response"
    cache_ai_response(cache_key, test_response)

    cached = get_cached_ai_response(cache_key)
    print(f"Cache retrieval: {'✓' if cached == test_response else '✗'}")

def test_intent_caching():
    """Test intent classification caching"""
    print("\n=== Тестирование кеширования классификации намерений ===")

    test_message = "Добавь задачу купить молоко завтра"
    mentions = "нет"

    # First call
    start_time = time.time()
    result1 = classify_intent_cached(test_message, mentions)
    first_call_time = time.time() - start_time

    # Second call (should be cached)
    start_time = time.time()
    result2 = classify_intent_cached(test_message, mentions)
    second_call_time = time.time() - start_time

    print(".2f")
    print(".2f")
    print(".2f")
    print(f"Intent type: {result1.get('type', 'unknown')}")

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