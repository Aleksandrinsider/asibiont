"""
Скрипт для логирования текущего золотого тарифа @sportfan3 в payment_history
"""
import os
from datetime import datetime, timezone, timedelta
from models import Session, User, PaymentHistory, SubscriptionTier

os.environ['DATABASE_URL'] = 'postgresql://postgres:hHmIDLimfDQMFAzkSZswCDKboRnZagYU@yamabiko.proxy.rlwy.net:12729/railway'

session = Session()

try:
    user = session.query(User).filter_by(username='sportfan3').first()
    
    if user:
        print(f"Найден пользователь: {user.username}, ID: {user.id}")
        print(f"Текущий тариф: {user.subscription_tier}")
        
        # Создаем запись в payment_history на год вперед
        end_date = datetime.now(timezone.utc) + timedelta(days=365)
        
        history_entry = PaymentHistory(
            user_id=user.id,
            telegram_username=user.username,
            action='manual_change',
            tier=SubscriptionTier.GOLD,
            duration_days=365,
            start_date=datetime.now(timezone.utc),
            end_date=end_date,
            details='{"reason": "Test user - permanent GOLD tier"}'
        )
        
        session.add(history_entry)
        session.commit()
        
        print(f"\n✅ Создана запись в payment_history")
        print(f"   Тариф: GOLD")
        print(f"   Активен до: {end_date.strftime('%Y-%m-%d %H:%M:%S')}")
        
    else:
        print("Пользователь @sportfan3 не найден")

except Exception as e:
    print(f"❌ Ошибка: {e}")
    session.rollback()
finally:
    session.close()
