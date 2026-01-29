from models import Session, PromoCode

session = Session()

print("\n" + "="*60)
print("ВСЕ ПРОМОКОДЫ В RAILWAY")
print("="*60)

promos = session.query(PromoCode).all()
for p in promos:
    print(f"\nКод: {p.code}")
    print(f"  Тариф: {p.tier.value}")
    print(f"  Скидка: {p.discount_percent}%")
    print(f"  Длительность: {p.duration_days} дней")
    print(f"  Лимит: {p.max_uses if p.max_uses else '∞'}")
    print(f"  Использований: {p.used_count}")

print("\n" + "="*60)
print(f"Всего промокодов: {len(promos)}")
print("="*60)

session.close()
