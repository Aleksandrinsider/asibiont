"""
Полная диагностика backend + проверка подписки
"""
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import User, Subscription, SubscriptionTier
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

print("=" * 80)
print("🔍 ДЕТАЛЬНАЯ ДИАГНОСТИКА BACKEND")
print("=" * 80)

try:
    aleksandr = session.query(User).filter_by(username="aleksandrinsider").first()
    
    print(f"\n👤 Пользователь: @{aleksandr.username}")
    print(f"   ID: {aleksandr.id}")
    print(f"   Telegram ID: {aleksandr.telegram_id}")
    print(f"   Subscription Tier: {aleksandr.subscription_tier}")
    
    # Проверка подписки
    print("\n" + "=" * 80)
    print("💳 ПРОВЕРКА ПОДПИСКИ:")
    print("=" * 80)
    
    subscription = session.query(Subscription).filter_by(user_id=aleksandr.id).first()
    if subscription:
        print(f"   ✅ Подписка найдена:")
        print(f"      Статус: {subscription.status}")
        print(f"      Тариф: {subscription.tier}")
        print(f"      Начало: {subscription.start_date}")
        print(f"      Конец: {subscription.end_date}")
    else:
        print(f"   ⚠️ Подписка не найдена в таблице subscriptions")
    
    # Проверяем tier - LIGHT блокирует делегирование
    if aleksandr.subscription_tier == SubscriptionTier.LIGHT:
        print(f"\n   ⚠️⚠️⚠️ ПРОБЛЕМА НАЙДЕНА!")
        print(f"   ❌ Тариф LIGHT блокирует раздел 'Поручаю я'!")
        print(f"   💡 В HTML кнопка имеет disabled-btn класс")
        print("   💡 onclick не сработает из-за Jinja2 условия")
    elif aleksandr.subscription_tier in [SubscriptionTier.STANDARD, SubscriptionTier.PREMIUM]:
        print(f"\n   ✅ Тариф {aleksandr.subscription_tier.value} позволяет делегирование")
    else:
        print(f"\n   ⚠️ Неизвестный тариф: {aleksandr.subscription_tier}")
    
    # Проверка FREE_ACCESS_MODE
    print("\n" + "=" * 80)
    print("🔓 РЕЖИМ БЕСПЛАТНОГО ДОСТУПА:")
    print("=" * 80)
    
    from config import FREE_ACCESS_MODE
    print(f"   FREE_ACCESS_MODE = {FREE_ACCESS_MODE}")
    if FREE_ACCESS_MODE:
        print(f"   ✅ Бесплатный доступ включен - все функции доступны")
    else:
        print(f"   ⚠️ Бесплатный доступ выключен - требуется подписка")
    
    # Симулируем логику из main.py
    print("\n" + "=" * 80)
    print("🖥️ СИМУЛЯЦИЯ BACKEND ЛОГИКИ:")
    print("=" * 80)
    
    from config import FREE_ACCESS_MODE
    
    # Проверка доступа
    has_subscription = subscription and subscription.status == 'active' if subscription else False
    tier_display = aleksandr.subscription_tier.value if aleksandr.subscription_tier else 'LIGHT'
    
    print(f"\n   has_subscription: {has_subscription}")
    print(f"   tier_display: {tier_display}")
    print(f"   FREE_ACCESS_MODE: {FREE_ACCESS_MODE}")
    
    # Проверяем условие доступа к дашборду
    if not FREE_ACCESS_MODE and (not subscription or subscription.status != 'active'):
        print(f"\n   ❌ Доступ к дашборду ЗАБЛОКИРОВАН")
        print(f"   Пользователь будет перенаправлен на /no_subscription")
    else:
        print(f"\n   ✅ Доступ к дашборду РАЗРЕШЕН")
    
    # Проверяем что передаётся в template
    print("\n" + "=" * 80)
    print("📤 ЧТО ПЕРЕДАЁТСЯ В TEMPLATE:")
    print("=" * 80)
    
    print(f"\n   subscription_tier = '{tier_display}'")
    
    # В HTML проверка для кнопки
    print(f"\n   HTML кнопки 'Поручаю я':")
    if tier_display == 'LIGHT':
        print(f"      class='... disabled-btn'  ❌")
        print("      onclick='{# blocked by tier #}'")
        print(f"      disabled  ❌")
        print(f"   ⚠️ Кнопка ЗАБЛОКИРОВАНА для LIGHT!")
    else:
        print(f"      class='...'  ✅")
        print("      onclick='filterContacts(\"delegating_by_me\")'  ✅")
        print(f"      (no disabled)  ✅")
        print(f"   ✅ Кнопка АКТИВНА!")
    
    # Проверяем данные delegating_by_me
    print("\n" + "=" * 80)
    print("📊 ДАННЫЕ DELEGATING_BY_ME:")
    print("=" * 80)
    
    from ai_integration.handlers import get_partners_list
    from models import Task, UserProfile
    from sqlalchemy import or_
    
    delegating_by_me = []
    
    try:
        my_delegated_tasks = session.query(Task).filter(
            Task.delegated_by == aleksandr.id,
            Task.delegated_to_username.isnot(None),
            Task.delegation_status.in_(['pending', 'accepted'])
        ).all()
        
        print(f"\n   Найдено {len(my_delegated_tasks)} делегированных задач")
        
        delegatee_usernames = set()
        for task in my_delegated_tasks:
            if task.delegated_to_username and task.delegated_to_username not in delegatee_usernames:
                delegatee_usernames.add(task.delegated_to_username)
                delegatee = session.query(User).filter(
                    or_(
                        User.username.ilike(task.delegated_to_username.replace('@', '')),
                        User.username.ilike(f'@{task.delegated_to_username.replace("@", "")}')
                    )
                ).first()
                if delegatee and delegatee.id != aleksandr.id:
                    delegatee_tasks = [
                        t for t in my_delegated_tasks if t.delegated_to_username == task.delegated_to_username]
                    task_count = len(delegatee_tasks)
                    task_titles = [t.title[:30] + '...' if len(t.title) > 30 else t.title for t in delegatee_tasks[:3]]
                    delegating_by_me.append({
                        'id': delegatee.id,
                        'username': delegatee.username,
                        'first_name': delegatee.first_name,
                        'reason': f'я делегировал {task_count} задач',
                        'tasks': task_titles,
                        'task_count': task_count
                    })
        
        print(f"\n   ✅ Контактов в delegating_by_me: {len(delegating_by_me)}")
        for contact in delegating_by_me:
            print(f"      • @{contact['username']} ({contact['task_count']} задач)")
        
    except Exception as e:
        print(f"\n   ❌ Ошибка при получении delegating_by_me: {e}")
        import traceback
        traceback.print_exc()
    
    # ИТОГОВАЯ ДИАГНОСТИКА
    print("\n" + "=" * 80)
    print("🎯 ИТОГОВАЯ ДИАГНОСТИКА:")
    print("=" * 80)
    
    if aleksandr.subscription_tier == SubscriptionTier.LIGHT:
        print(f"\n❌ ПРОБЛЕМА: Тариф LIGHT блокирует раздел 'Поручаю я'!")
        print(f"\n💡 РЕШЕНИЕ:")
        print(f"   1. Обновить подписку на STANDARD или PREMIUM")
        print(f"   2. Или включить FREE_ACCESS_MODE в config.py")
        print(f"   3. Или изменить subscription_tier в БД:")
        print(f"      UPDATE users SET subscription_tier = 'STANDARD'")
        print(f"      WHERE username = 'aleksandrinsider';")
    elif len(delegating_by_me) > 0:
        print(f"\n✅ ВСЁ ПРАВИЛЬНО!")
        print(f"   • Тариф: {aleksandr.subscription_tier.value}")
        print(f"   • Контактов: {len(delegating_by_me)}")
        print(f"   • Кнопка активна: ДА")
        print(f"\n🔄 Если всё равно не видно:")
        print(f"   1. Обновите страницу (Ctrl+F5)")
        print(f"   2. Очистите кэш браузера")
        print(f"   3. Откройте в режиме инкогнито")
        print(f"   4. Проверьте консоль браузера (F12) на ошибки JS")
    else:
        print(f"\n⚠️ Данные есть в БД, но не попадают в delegating_by_me")
        print(f"   Проверьте логику выше")

finally:
    session.close()

print("\n" + "=" * 80)
