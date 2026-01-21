import os
if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

from models import SessionLocal, User, Subscription, SubscriptionTier
from datetime import datetime
import pytz

try:
    session = SessionLocal()
    
    print("=== Синхронизация users.subscription_tier с subscriptions.tier ===\n")
    
    # Получаем всех пользователей с активными подписками
    active_subscriptions = session.query(Subscription).filter_by(status='active').all()
    
    print(f"Найдено активных подписок: {len(active_subscriptions)}\n")
    
    updated_count = 0
    
    for sub in active_subscriptions:
        user = session.query(User).filter_by(id=sub.user_id).first()
        if not user:
            print(f"⚠️ Пользователь ID {sub.user_id} не найден для подписки {sub.id}")
            continue
        
        # Проверяем, не истекла ли подписка
        now = datetime.now(pytz.UTC)
        if sub.end_date and sub.end_date.tzinfo is None:
            sub.end_date = sub.end_date.replace(tzinfo=pytz.UTC)
        
        if sub.end_date and sub.end_date < now:
            print(f"⏭️ @{user.username}: подписка истекла {sub.end_date}, пропускаем")
            continue
        
        # Сравниваем тарифы
        user_tier_str = str(user.subscription_tier).split('.')[-1]
        sub_tier_str = str(sub.tier).split('.')[-1]
        
        if user_tier_str != sub_tier_str:
            print(f"🔄 @{user.username} (ID {user.id}):")
            print(f"   users: {user.subscription_tier} → subscriptions: {sub.tier}")
            user.subscription_tier = sub.tier
            updated_count += 1
    
    session.commit()
    print(f"\n✅ Обновлено пользователей: {updated_count}")
    
    # Проверка результата
    print("\n" + "="*60)
    print("=== Проверка Gold пользователей после синхронизации ===\n")
    
    gold_users = session.query(User).filter(User.subscription_tier == SubscriptionTier.GOLD).all()
    print(f"Gold пользователей в users: {len(gold_users)}\n")
    
    for user in gold_users:
        print(f"  - @{user.username} (ID: {user.id})")
    
    session.close()
    
except Exception as e:
    print(f"\n❌ Ошибка: {e}")
    import traceback
    traceback.print_exc()
