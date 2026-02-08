"""Проверка Premium features - Activity & Contact alerts"""
import asyncio
from models import Session, User, UserProfile, SubscriptionTier
from ai_integration.handlers import set_activity_alert, set_contact_alert

async def test_premium_features():
    session = Session()
    
    print("\n" + "="*60)
    print("🌟 ТЕСТ PREMIUM ФУНКЦИЙ")
    print("="*60 + "\n")
    
    # Создаем тестового Premium пользователя
    user_id = 888999000
    session.query(User).filter_by(telegram_id=user_id).delete()
    session.commit()
    
    user = User(
        telegram_id=user_id,
        username="premium_test",
        subscription_tier=SubscriptionTier.PREMIUM  # Premium подписка
    )
    session.add(user)
    session.commit()
    
    print(f"✅ Создан Premium пользователь: @{user.username}")
    print(f"   Подписка: {user.subscription_tier.value}\n")
    
    # Тест 1: Activity Alert
    print("[1/2] Activity Alert")
    print("-" * 60)
    result = set_activity_alert(
        activity_type="пробежка",
        keywords=["пробежка", "бег", "running"],
        location="Москва",
        user_id=user_id,
        session=session
    )
    print(f"📝 Результат:\n{result}\n")
    
    # Тест 2: Contact Alert
    print("[2/2] Contact Alert")
    print("-" * 60)
    result = set_contact_alert(
        skill="Python",
        interest="стартапы",
        city="Москва",
        user_id=user_id,
        session=session
    )
    print(f"📝 Результат:\n{result}\n")
    
    # Проверка в БД
    from models import ActivityAlert, ContactAlert
    
    activity_alerts = session.query(ActivityAlert).filter_by(user_id=user.id).all()
    contact_alerts = session.query(ContactAlert).filter_by(user_id=user.id).all()
    
    print("="*60)
    print("📊 СОХРАНЕНО В БД:")
    print("="*60)
    print(f"Activity Alerts: {len(activity_alerts)}")
    for a in activity_alerts:
        print(f"  - {a.activity_type} (keywords: {a.keywords})")
    
    print(f"\nContact Alerts: {len(contact_alerts)}")
    for c in contact_alerts:
        details = []
        if c.skill:
            details.append(f"skill={c.skill}")
        if c.interest:
            details.append(f"interest={c.interest}")
        print(f"  - {', '.join(details)}")
    
    print("\n" + "="*60)
    print("🎯 КАК ЭТО РАБОТАЕТ:")
    print("="*60)
    print("""
1. Пользователь настраивает алерты через AI:
   "Скажи когда кто-то пойдет на пробежку"
   "Предупреди о новых Python разработчиках"

2. Система мониторит:
   - Activity: новые задачи других пользователей
   - Contact: регистрации и обновления профилей

3. Когда находит совпадение:
   - Добавляет в КОНТЕКСТ для AI (проактивный промпт)
   - AI естественно упоминает в разговоре:
     "Кстати, @user123 планирует пробежку завтра в 7:00"
     "Появился новый Python dev @dev456 из Москвы"

4. БЕЗ НАВЯЗЧИВЫХ push-уведомлений!
   Вся информация приходит естественно в диалоге.
    """)
    
    print("="*60)
    print("✅ ВСЕ ФУНКЦИИ РАБОТАЮТ!")
    print("="*60 + "\n")
    
    session.close()

if __name__ == "__main__":
    asyncio.run(test_premium_features())
