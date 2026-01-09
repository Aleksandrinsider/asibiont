import requests
import json

def test_api_endpoints():
    """Тестирование основных API эндпоинтов"""

    base_url = "http://localhost:3000"
    session = requests.Session()

    print("🧪 ТЕСТИРОВАНИЕ API ЭНДПОИНТОВ")
    print("=" * 50)

    # Тесты без аутентификации
    tests = [
        ("GET", "/health", None, "Проверка здоровья сервера"),
        ("GET", "/login", None, "Страница входа"),
    ]

    for method, endpoint, data, description in tests:
        try:
            if method == "GET":
                response = session.get(f"{base_url}{endpoint}")
            elif method == "POST":
                response = session.post(f"{base_url}{endpoint}", json=data)

            status = "✅" if response.status_code < 400 else "❌"
            print(f"{status} {description}: {response.status_code}")

            if response.status_code >= 400:
                try:
                    error_data = response.json()
                    print(f"   Ошибка: {error_data}")
                except:
                    print(f"   Ответ: {response.text[:100]}")

        except Exception as e:
            print(f"❌ {description}: Ошибка подключения - {e}")

    print("\n📝 Для тестирования аутентифицированных эндпоинтов:")
    print("   1. Откройте браузер и перейдите на http://localhost:8000")
    print("   2. Авторизуйтесь через Telegram")
    print("   3. Протестируйте функции в веб-интерфейсе")

if __name__ == "__main__":
    test_api_endpoints()