"""
Инструкция для логина на production dashboard
"""
import os
if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

from config import ADMIN_SECRET

print("="*60)
print("ИНСТРУКЦИЯ ДЛЯ ЛОГИНА НА PRODUCTION DASHBOARD")
print("="*60)

BASE_URL = "https://task-production-31b6.up.railway.app"
USER_ID = 146333757

print(f"\n1️⃣ Откройте эту ссылку в браузере:")
print(f"\n   {BASE_URL}/direct_login?user_id={USER_ID}\n")

print("2️⃣ Вас автоматически перенаправит на dashboard с активной сессией")

print("\n3️⃣ На dashboard вы должны увидеть:")
print("   - Профиль: @aleksandrinsider, Москва")
print("   - Задачи: 'Сделать перерыв' и 'Проверить почту'")
print("   - Подписка: активна до 31.01.2026")

print("\n4️⃣ Откройте консоль браузера (F12) и проверьте:")
print("   - Network tab: запросы к /api/tasks должны возвращать 200 OK")
print("   - Console: не должно быть JavaScript ошибок")

print("\n" + "="*60)
print("ПОДОЖДИТЕ 1-2 МИНУТЫ ПОКА RAILWAY ЗАДЕПЛОИТ ИЗМЕНЕНИЯ!")
print("="*60)
