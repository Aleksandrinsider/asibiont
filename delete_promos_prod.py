"""
Скрипт для удаления промокодов с ID 10 и 11 из продакшн БД
и проверки почему они постоянно появляются
"""
import os
import sys

# НЕ ставим LOCAL=1 - работаем с продакшн БД
if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

from models import Session, PromoCode

session = Session()

print("\n=== Проверка промокодов ===")
print(f"Database URL: {session.bind.url}")

# Находим промокоды с ID 10 и 11
promos_to_delete = session.query(PromoCode).filter(PromoCode.id.in_([10, 11])).all()

if promos_to_delete:
    print(f"\nНайдено {len(promos_to_delete)} промокодов для удаления:")
    for promo in promos_to_delete:
        print(f"  ID={promo.id}, code={promo.code}, tier={promo.tier}, created={promo.created_at}")
    
    # Удаляем
    confirm = input("\nУдалить эти промокоды? (yes/no): ")
    if confirm.lower() == 'yes':
        for promo in promos_to_delete:
            session.delete(promo)
        session.commit()
        print("✓ Промокоды удалены")
    else:
        print("Отменено")
else:
    print("\nПромокоды с ID 10 и 11 не найдены")

# Показываем все существующие промокоды
print("\n=== Все промокоды в БД ===")
all_promos = session.query(PromoCode).order_by(PromoCode.id).all()
print(f"Всего промокодов: {len(all_promos)}")
for p in all_promos:
    print(f"  ID={p.id}: {p.code} - tier={p.tier}, uses={p.used_count}/{p.max_uses}, expires={p.expires_at}")

session.close()

print("\n=== Проверка кода main.py на автосоздание промокодов ===")
# Проверяем main.py на наличие создания промокодов
with open('main.py', 'r', encoding='utf-8') as f:
    content = f.read()
    
# Ищем создание промокодов
import re
promo_creations = re.findall(r'PromoCode\([^)]+\)', content)
print(f"Найдено {len(promo_creations)} мест создания промокодов в main.py:")
for i, creation in enumerate(promo_creations, 1):
    print(f"{i}. {creation[:100]}...")

print("\n✓ Проверка завершена")
