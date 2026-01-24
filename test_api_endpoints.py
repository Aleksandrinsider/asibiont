#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тест API endpoints для отправки сообщений и постов
"""

import requests
import json

def test_local_api():
    """Тестируем локальный API"""
    base_url = "http://localhost:8080"
    
    print("🧪 Тестирование локального API...")
    
    # Тест health endpoint
    try:
        response = requests.get(f"{base_url}/health")
        print(f"✅ Health check: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"❌ Health check failed: {e}")
        return
    
    # Тест главной страницы
    try:
        response = requests.get(f"{base_url}/dashboard")
        print(f"✅ Dashboard: {response.status_code}")
    except Exception as e:
        print(f"❌ Dashboard failed: {e}")

def test_production_api():
    """Тестируем production API"""
    base_url = "https://task-production-3a02.up.railway.app"
    
    print("🧪 Тестирование production API...")
    
    # Тест health endpoint
    try:
        response = requests.get(f"{base_url}/health", timeout=10)
        print(f"✅ Production health: {response.status_code} - {response.text}")
    except requests.exceptions.Timeout:
        print(f"⏱️ Production timeout - сервер может перезапускаться")
    except requests.exceptions.ConnectionError:
        print(f"🔌 Production connection error - сервер недоступен")
    except Exception as e:
        print(f"❌ Production health failed: {e}")

if __name__ == "__main__":
    print("="*60)
    print("ТЕСТИРОВАНИЕ API ENDPOINTS")
    print("="*60)
    
    test_local_api()
    print()
    test_production_api()
    
    print("\n💡 Рекомендации:")
    print("1. Если локальный API работает, но production нет - дождитесь перезапуска Railway")
    print("2. Если оба не работают - проверьте код endpoints")
    print("3. 502 ошибка обычно означает что сервер перезапускается")