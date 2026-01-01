"""
Тест API endpoints production
"""
import requests
import json

BASE_URL = "https://task-production-31b6.up.railway.app"

print("🔍 Тестирование API endpoints...\n")

# Создаем сессию
session = requests.Session()

# 1. Логин
print("1️⃣ Логин...")
login_data = {"telegram_id": "146333757"}
response = session.post(f"{BASE_URL}/login", data=login_data, allow_redirects=False)
print(f"   Status: {response.status_code}")
print(f"   Cookies: {dict(response.cookies)}")

# 2. Проверка /api/tasks
print("\n2️⃣ Получение задач /api/tasks...")
response = session.get(f"{BASE_URL}/api/tasks")
print(f"   Status: {response.status_code}")
if response.status_code == 200:
    tasks = response.json()
    print(f"   Задач получено: {len(tasks)}")
    for task in tasks[:3]:  # Показываем первые 3
        print(f"   - {task.get('title')} [{task.get('status')}]")
else:
    print(f"   Ошибка: {response.text}")

# 3. Проверка /api/profile
print("\n3️⃣ Получение профиля /api/profile...")
response = session.get(f"{BASE_URL}/api/profile")
print(f"   Status: {response.status_code}")
if response.status_code == 200:
    profile = response.json()
    print(f"   Telegram ID: {profile.get('telegram_id')}")
    print(f"   Username: {profile.get('username')}")
    print(f"   Город: {profile.get('city')}")
    print(f"   Интересы: {profile.get('interests')}")
else:
    print(f"   Ошибка: {response.text}")

# 4. Проверка /api/statistics
print("\n4️⃣ Получение статистики /api/statistics...")
response = session.get(f"{BASE_URL}/api/statistics")
print(f"   Status: {response.status_code}")
if response.status_code == 200:
    stats = response.json()
    print(f"   В работе: {stats.get('in_progress', 0)}")
    print(f"   Просрочено: {stats.get('overdue', 0)}")
    print(f"   Выполнено: {stats.get('completed', 0)}")
else:
    print(f"   Ошибка: {response.text}")

print("\n" + "="*60)
print("Если все 200 OK - проблема в браузере/JavaScript")
print("Откройте браузер Console (F12) и проверьте ошибки")
print("="*60)
