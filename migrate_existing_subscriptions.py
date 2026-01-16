"""
Миграция существующих активных подписок в payment_history
для защиты от потери данных
"""
import os
from datetime import datetime, timezone
from models import Session, Subscription, User, PaymentHistory

os.environ['DATABASE_URL'] = 'postgresql://postgres:hHmIDLimfDQMFAzkSZswCDKboRnZagYU@yamabiko.proxy.rlwy.net:12729/railway'

session = Session()

try:
    print("=== Миграция активных подписок в payment_history ===\n")
    
    # Получаем все активные подписки
    active_subs = session.query(Subscription).filter_by(status='active').all()
    print(f"Найдено активных подписок: {len(active_subs)}\n")
    
    now = datetime.now(timezone.utc)
    migrated = 0
    skipped = 0
    
    for sub in active_subs:
        # Проверяем, не истекла ли подписка
        end_date = sub.end_date
        if end_date and end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)
        
        if end_date and end_date < now:
            print(f"⏭️  Пропущена истекшая подписка: User ID={sub.user_id}")
            skipped += 1
            continue
        
        # Получаем пользователя
        user = session.query(User).filter_by(id=sub.user_id).first()
        if not user:
            print(f"⚠️  Пользователь не найден: User ID={sub.user_id}")
            skipped += 1
            continue
        
        # Проверяем, нет ли уже записи в payment_history
        existing = session.query(PaymentHistory).filter(
            PaymentHistory.user_id == user.id,
            PaymentHistory.end_date == sub.end_date
        ).first()
        
        if existing:
            print(f"⏭️  Запись уже существует: {user.username} (ID={user.id}), tier={sub.tier.value}")
            skipped += 1
            continue
        
        # Создаем запись в payment_history
        history_entry = PaymentHistory(
            user_id=user.id,
            telegram_username=user.username,
            action='migration',
            tier=sub.tier,
            duration_days=(end_date - now).days if end_date else 365,
            start_date=sub.start_date if sub.start_date else now,
            end_date=end_date,
            details='{"reason": "Migrated from existing subscription", "plan": "' + sub.plan + '"}'
        )
        
        session.add(history_entry)
        migrated += 1
        
        print(f"✅ Мигрирована подписка: {user.username} (ID={user.id})")
        print(f"   Тариф: {sub.tier.value}")
        print(f"   Активна до: {end_date.strftime('%Y-%m-%d %H:%M:%S') if end_date else 'бессрочно'}\n")
    
    session.commit()
    
    print(f"\n{'='*50}")
    print(f"✅ Миграция завершена")
    print(f"   Мигрировано: {migrated}")
    print(f"   Пропущено: {skipped}")
    print(f"{'='*50}")

except Exception as e:
    print(f"\n❌ Ошибка при миграции: {e}")
    session.rollback()
    import traceback
    traceback.print_exc()
finally:
    session.close()
