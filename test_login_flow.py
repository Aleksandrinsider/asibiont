"""
Проверка что возвращает production после логина
"""
import requests

BASE_URL = "https://task-production-31b6.up.railway.app"
USER_ID = 146333757

print("🔍 Тест прямого логина и проверка API\n")

session = requests.Session()

# 1. Логин через direct_login
print("1️⃣ Выполняю логин через /direct_login...")
response = session.get(f"{BASE_URL}/direct_login?user_id={USER_ID}", allow_redirects=False)
print(f"   Status: {response.status_code}")
print(f"   Location: {response.headers.get('Location')}")
print(f"   Cookies после логина: {dict(session.cookies)}")

if response.status_code == 302:
    print("   ✅ Редирект на dashboard")
else:
    print(f"   ❌ Ошибка: {response.text}")

# 2. Следуем редиректу на dashboard
print("\n2️⃣ Открываем /dashboard с сессией...")
response = session.get(f"{BASE_URL}/dashboard")
print(f"   Status: {response.status_code}")
print(f"   Cookies: {dict(session.cookies)}")

# Проверяем содержимое HTML
html = response.text
if "криптовалюты" in html:
    print("   ✅ Профиль найден: 'криптовалюты'")
else:
    print("   ❌ Профиль НЕ найден в HTML")

if "Проверить почту" in html or "Сделать перерыв" in html:
    print("   ✅ Задачи найдены в HTML")
else:
    print("   ❌ Задачи НЕ найдены в HTML")

if "logged_in" in html or "aleksandrinsider" in html:
    print("   ✅ Пользователь залогинен")
else:
    print("   ❌ Пользователь НЕ залогинен")

# 3. Проверяем API
print("\n3️⃣ Проверяем /api/tasks...")
response = session.get(f"{BASE_URL}/api/tasks")
print(f"   Status: {response.status_code}")
if response.status_code == 200:
    data = response.json()
    tasks = data.get('tasks', [])
    print(f"   ✅ Получено задач: {len(tasks)}")
    for task in tasks:
        print(f"      - {task['title']} [{task['status']}]")
else:
    print(f"   ❌ {response.text}")

print("\n4️⃣ Проверяем /api/profile...")
response = session.get(f"{BASE_URL}/api/profile")
print(f"   Status: {response.status_code}")
if response.status_code == 200:
    data = response.json()
    print(f"   ✅ Username: {data.get('username')}")
    print(f"   ✅ Интересы: {data.get('interests')}")
else:
    print(f"   ❌ {response.text}")

print("\n" + "="*60)
print("ДИАГНОСТИКА:")
if session.cookies.get('AIOHTTP_SESSION'):
    print("✅ Cookie сессии установлена")
else:
    print("❌ Cookie сессии НЕ установлена - проблема в /direct_login")
print("="*60)
