"""
Проверка Railway API
"""
import requests
import os
from dotenv import load_dotenv

load_dotenv()

RAILWAY_URL = "https://web-production-f6d2.up.railway.app"

print("=" * 80)
print("🚂 ПРОВЕРКА RAILWAY API")
print("=" * 80)

# Проверка главной страницы
print(f"\n🌐 GET {RAILWAY_URL}/")
try:
    response = requests.get(RAILWAY_URL, timeout=10)
    print(f"   Статус: {response.status_code}")
    if response.status_code == 200:
        print(f"   ✅ Сайт доступен")
    else:
        print(f"   ⚠️ Неожиданный статус")
except Exception as e:
    print(f"   ❌ Ошибка: {e}")

# Проверка dashboard (требует авторизации)
print(f"\n🔐 GET {RAILWAY_URL}/dashboard")
try:
    response = requests.get(f"{RAILWAY_URL}/dashboard", timeout=10, allow_redirects=False)
    print(f"   Статус: {response.status_code}")
    if response.status_code == 302:
        print(f"   ✅ Редирект на авторизацию (ожидаемо)")
    elif response.status_code == 200:
        print(f"   ⚠️ Доступ без авторизации (неожиданно)")
    else:
        print(f"   ⚠️ Статус: {response.status_code}")
except Exception as e:
    print(f"   ❌ Ошибка: {e}")

# Проверка webhook (bot endpoint)
print(f"\n🤖 POST {RAILWAY_URL}/webhook (пустой)")
try:
    response = requests.post(f"{RAILWAY_URL}/webhook", json={}, timeout=10)
    print(f"   Статус: {response.status_code}")
    if response.status_code in [200, 400]:
        print(f"   ✅ Endpoint доступен")
    elif response.status_code == 502:
        print(f"   ❌ 502 Bad Gateway - service перезапускается или упал")
    else:
        print(f"   ⚠️ Неожиданный статус")
except requests.exceptions.Timeout:
    print(f"   ❌ Timeout - service не отвечает")
except Exception as e:
    print(f"   ❌ Ошибка: {e}")

print("\n" + "=" * 80)
print("💡 ВЫВОДЫ:")
print("=" * 80)
print("""
Если все endpoints возвращают 502:
  → Railway service перезапускается
  → Подождите 1-2 минуты

Если главная страница 200, но /dashboard 502:
  → Частичный сбой, проверьте Railway логи

Если всё 200/302:
  → Backend работает
  → Проблема в кэше браузера
  → Ctrl+F5 или инкогнито
""")
print("=" * 80)
