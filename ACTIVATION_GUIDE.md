# Активация подписки для пользователя 146333757

Локальное подключение к Railway PostgreSQL заблокировано. Используйте один из методов:

## Метод 1: Railway CLI (рекомендуется)

```bash
railway run python -c "from models import Session, User, Subscription, SubscriptionTier; from datetime import datetime, timedelta; db = Session(); user = db.query(User).filter_by(telegram_id=146333757).first(); user.subscription_tier = SubscriptionTier.SILVER if user else None; sub = db.query(Subscription).filter_by(user_id=user.id).first() if user else None; sub and setattr(sub, 'tier', SubscriptionTier.SILVER) or db.add(Subscription(user_id=user.id, tier=SubscriptionTier.SILVER, status='active', start_date=datetime.now(), end_date=datetime.now() + timedelta(days=30))); db.commit(); print(f'✅ SILVER активирована для {user.telegram_id}' if user else '❌ Пользователь не найден'); db.close()"
```

## Метод 2: SQL Console в Railway Dashboard

1. Откройте Railway Dashboard → ваш проект → PostgreSQL → Data
2. Выполните SQL:

```sql
-- Шаг 1: Проверить пользователя
SELECT id, telegram_id, first_name, subscription_tier 
FROM users 
WHERE telegram_id = 146333757;

-- Шаг 2: Обновить tier (запомните user_id из предыдущего запроса)
UPDATE users 
SET subscription_tier = 'SILVER' 
WHERE telegram_id = 146333757;

-- Шаг 3: Активировать подписку (замените USER_ID на id из шага 1)
INSERT INTO subscriptions (user_id, tier, status, start_date, end_date)
VALUES (USER_ID, 'SILVER', 'active', NOW(), NOW() + INTERVAL '30 days')
ON CONFLICT (user_id) 
DO UPDATE SET
    tier = 'SILVER',
    status = 'active',
    start_date = NOW(),
    end_date = NOW() + INTERVAL '30 days';
```

## Метод 3: Добавить endpoint в бот

Добавьте в handlers.py временный хендлер:

```python
@router.message(Command("activate_silver"))
async def activate_silver_command(message: Message):
    if message.from_user.id != 146333757:
        return await message.answer("❌ Недостаточно прав")
    
    from models import Session, User, Subscription, SubscriptionTier
    from datetime import datetime, timedelta
    
    with Session() as db:
        user = db.query(User).filter_by(telegram_id=146333757).first()
        if user:
            user.subscription_tier = SubscriptionTier.SILVER
            sub = db.query(Subscription).filter_by(user_id=user.id).first()
            if sub:
                sub.tier = SubscriptionTier.SILVER
                sub.status = 'active'
                sub.end_date = datetime.utcnow() + timedelta(days=30)
            else:
                db.add(Subscription(
                    user_id=user.id,
                    tier=SubscriptionTier.SILVER,
                    status='active',
                    start_date=datetime.utcnow(),
                    end_date=datetime.utcnow() + timedelta(days=30)
                ))
            db.commit()
            await message.answer(f"✅ SILVER активирована до {sub.end_date}")
```

Затем отправьте `/activate_silver` в бота.
