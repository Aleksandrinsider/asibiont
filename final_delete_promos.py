"""
Окончательное удаление промокодов 10 и 11 из продакшн БД
"""
import os
if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

from models import Session, PromoCode

session = Session()
print(f"Database: {session.bind.url}")

# Удаляем промокоды с ID 10 и 11
deleted = session.query(PromoCode).filter(PromoCode.id.in_([10, 11])).delete(synchronize_session=False)
session.commit()

print(f"✓ Deleted {deleted} promo codes")

# Проверяем результат
remaining = session.query(PromoCode).order_by(PromoCode.id).all()
print(f"\nRemaining promo codes: {len(remaining)}")
for p in remaining:
    print(f"  ID={p.id}: {p.code} (tier={p.tier})")

session.close()
