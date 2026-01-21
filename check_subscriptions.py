import os
if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

from models import SessionLocal, User, Subscription, SubscriptionTier

try:
    session = SessionLocal()
    
    print("=== Сравнение users.subscription_tier и subscriptions.tier ===\n")
    
    # Проверяем ID 42, 43, 50, 51
    user_ids = [42, 43, 50, 51]
    
    for user_id in user_ids:
        user = session.query(User).filter_by(id=user_id).first()
        if not user:
            print(f"ID {user_id}: ❌ Пользователь не найден\n")
            continue
        
        print(f"{'='*60}")
        print(f"ID {user_id}: @{user.username}")
        print(f"Telegram ID: {user.telegram_id}")
        print(f"users.subscription_tier: {user.subscription_tier}")
        
        # Проверяем подписку
        subscription = session.query(Subscription).filter_by(user_id=user.id).first()
        if subscription:
            print(f"✅ Запись в subscriptions найдена:")
            print(f"   subscription_id: {subscription.id}")
            print(f"   subscriptions.tier: {subscription.tier}")
            print(f"   status: {subscription.status}")
            print(f"   start_date: {subscription.start_date}")
            print(f"   end_date: {subscription.end_date}")
            
            # Проверяем расхождение
            user_tier_str = str(user.subscription_tier).split('.')[-1]
            sub_tier_str = str(subscription.tier).split('.')[-1]
            
            if user_tier_str != sub_tier_str:
                print(f"   ⚠️ РАСХОЖДЕНИЕ: users.subscription_tier ({user_tier_str}) ≠ subscriptions.tier ({sub_tier_str})")
        else:
            print(f"❌ Записи в subscriptions НЕТ")
        print()
    
    print("\n" + "="*60)
    print("=== Все активные Gold подписки в subscriptions ===\n")
    
    gold_subscriptions = session.query(Subscription).filter(
        Subscription.tier == SubscriptionTier.GOLD,
        Subscription.status == 'active'
    ).all()
    
    print(f"Найдено активных Gold подписок: {len(gold_subscriptions)}\n")
    
    for sub in gold_subscriptions:
        user = session.query(User).filter_by(id=sub.user_id).first()
        if user:
            print(f"  - User ID {user.id}: @{user.username}")
            print(f"    Telegram ID: {user.telegram_id}")
            print(f"    subscriptions.tier: {sub.tier}")
            print(f"    users.subscription_tier: {user.subscription_tier}")
            
            user_tier_str = str(user.subscription_tier).split('.')[-1]
            sub_tier_str = str(sub.tier).split('.')[-1]
            
            if user_tier_str != sub_tier_str:
                print(f"    ⚠️ НЕСООТВЕТСТВИЕ!")
            print()
    
    print("\n" + "="*60)
    print("=== Все Gold пользователи в users ===\n")
    
    gold_users = session.query(User).filter(User.subscription_tier == SubscriptionTier.GOLD).all()
    print(f"Найдено: {len(gold_users)}\n")
    
    for user in gold_users:
        sub = session.query(Subscription).filter_by(user_id=user.id).first()
        print(f"  - User ID {user.id}: @{user.username}")
        print(f"    users.subscription_tier: {user.subscription_tier}")
        if sub:
            print(f"    subscriptions.tier: {sub.tier} (status: {sub.status})")
        else:
            print(f"    ⚠️ Нет записи в subscriptions")
        print()
    
    session.close()
    
except Exception as e:
    print(f"\n❌ Ошибка: {e}")
    import traceback
    traceback.print_exc()
