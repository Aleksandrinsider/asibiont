import psycopg2
from datetime import datetime, timedelta

conn = psycopg2.connect(
    host='shinkansen.proxy.rlwy.net',
    port=27224,
    user='postgres',
    password='sANXAzJHOtUZkUeeiUUvdNqgxBuAVtdd',
    database='railway'
)

cur = conn.cursor()

telegram_id = 146333757
tier = 'BRONZE'
end_date = datetime.utcnow() + timedelta(days=365)  # Год для BRONZE

# Обновить tier пользователя
cur.execute(
    "UPDATE users SET subscription_tier = %s WHERE telegram_id = %s",
    (tier, telegram_id)
)

# Создать или обновить подписку
cur.execute("SELECT id FROM users WHERE telegram_id = %s", (telegram_id,))
user_id = cur.fetchone()[0]

cur.execute(
    """
    INSERT INTO subscriptions (user_id, telegram_id, tier, status, start_date, end_date)
    VALUES (%s, %s, %s, 'active', NOW(), %s)
    ON CONFLICT (user_id) 
    DO UPDATE SET 
        tier = %s,
        status = 'active',
        start_date = NOW(),
        end_date = %s
    """,
    (user_id, telegram_id, tier, end_date, tier, end_date)
)

conn.commit()

print(f"✅ Подписка SILVER активирована для {telegram_id}")
print(f"📅 Действительна до: {end_date.strftime('%Y-%m-%d %H:%M:%S')}")

# Проверка
cur.execute("SELECT subscription_tier FROM users WHERE telegram_id = %s", (telegram_id,))
print(f"✔️  User tier: {cur.fetchone()[0]}")

cur.execute("SELECT tier, status, end_date FROM subscriptions WHERE telegram_id = %s", (telegram_id,))
sub = cur.fetchone()
if sub:
    print(f"✔️  Subscription: {sub[0]} | {sub[1]} | До {sub[2]}")

conn.close()
