"""
Отладка: проверка API endpoints как из браузера
"""
import requests
import json

BASE_URL = "https://task-production-31b6.up.railway.app"
USER_ID = 146333757

print("🔍 Симуляция браузера для отладки dashboard\n")

# Создаем сессию (как браузер)
session = requests.Session()

# 1. Заходим на dashboard без логина
print("1️⃣ Открываем /dashboard без логина...")
response = session.get(f"{BASE_URL}/dashboard", allow_redirects=True)
print(f"   Status: {response.status_code}")
print(f"   URL: {response.url}")
print(f"   Cookies: {dict(session.cookies)}")

# Проверяем что в HTML
if "Log in with Telegram" in response.text or "Telegram" in response.text:
    print("   ✅ Виджет логина найден на странице")
else:
    print("   ⚠️ Виджет логина НЕ найден")

# 2. Симулируем Telegram auth
print("\n2️⃣ Симулируем Telegram авторизацию...")
# В реальности Telegram widget делает redirect на /tg_auth с параметрами
# Мы создадим валидную сессию напрямую через cookie

# Создаем сессию вручную (как делает auth_handler)
import hashlib
from aiohttp_session import SimpleCookieStorage
session_data = {'user_id': USER_ID}
# SimpleCookieStorage использует JSON для куки
cookie_value = json.dumps(session_data)

# Устанавливаем cookie
session.cookies.set('AIOHTTP_SESSION', cookie_value, domain='.railway.app', path='/')

print(f"   Установлена cookie с user_id: {USER_ID}")
print(f"   Cookie value: {cookie_value}")

# 3. Заходим на dashboard со сессией
print("\n3️⃣ Открываем /dashboard с сессией...")
response = session.get(f"{BASE_URL}/dashboard")
print(f"   Status: {response.status_code}")
print(f"   Cookies: {dict(session.cookies)}")

# Проверяем что в HTML
if "aleksandrinsider" in response.text:
    print("   ✅ Username найден в HTML")
else:
    print("   ⚠️ Username НЕ найден в HTML")

if "Проверить почту" in response.text or "Сделать перерыв" in response.text:
    print("   ✅ Задачи найдены в HTML")
else:
    print("   ⚠️ Задачи НЕ найдены в HTML")

# 4. Пробуем API endpoints
print("\n4️⃣ Проверяем API /api/tasks...")
response = session.get(f"{BASE_URL}/api/tasks")
print(f"   Status: {response.status_code}")
if response.status_code == 200:
    try:
        data = response.json()
        tasks = data.get('tasks', [])
        print(f"   ✅ Получено задач: {len(tasks)}")
        for task in tasks:
            print(f"      - {task.get('title')} [{task.get('status')}]")
    except:
        print(f"   ⚠️ Ответ: {response.text[:200]}")
else:
    print(f"   ❌ Ошибка: {response.text}")

print("\n5️⃣ Проверяем API /api/profile...")
response = session.get(f"{BASE_URL}/api/profile")
print(f"   Status: {response.status_code}")
if response.status_code == 200:
    try:
        data = response.json()
        print(f"   ✅ Username: {data.get('username')}")
        print(f"   ✅ Город: {data.get('city')}")
        print(f"   ✅ Интересы: {data.get('interests')}")
    except:
        print(f"   ⚠️ Ответ: {response.text[:200]}")
else:
    print(f"   ❌ Ошибка: {response.text}")

print("\n" + "="*60)
print("ВЫВОД:")
print("- Если API возвращают 401: проблема с сессией")
print("- Если API возвращают 200 но данных нет: проблема в БД")
print("- Если dashboard HTML не содержит данные: проблема в template")
print("="*60)
