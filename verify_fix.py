#!/usr/bin/env python3
"""
Проверка конфигурации после настройки Railway
"""

def quick_status_check():
    print("⚡ Быстрая проверка статуса системы")
    print("=" * 40)
    
    # Проверяем критические компоненты
    checks = []
    
    try:
        from config import LOCAL, YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY
        checks.append(("Config загружен", True, "✅"))
        checks.append(("LOCAL режим", LOCAL, "🔧" if LOCAL else "✅"))
        checks.append(("YOOKASSA_SHOP_ID", bool(YOOKASSA_SHOP_ID), "✅" if YOOKASSA_SHOP_ID else "❌"))
        checks.append(("YOOKASSA_SECRET_KEY", bool(YOOKASSA_SECRET_KEY), "✅" if YOOKASSA_SECRET_KEY else "❌"))
    except Exception as e:
        checks.append(("Config загружен", False, f"❌ {e}"))
        
    try:
        from payments import create_payment
        checks.append(("Payments модуль", True, "✅"))
    except Exception as e:
        checks.append(("Payments модуль", False, f"❌ {e}"))
        
    try:
        from models import Session, PromoCode
        session = Session()
        promo_count = session.query(PromoCode).count()
        session.close()
        checks.append(("База данных", True, "✅"))
        checks.append(("Промокоды в БД", promo_count, f"✅ {promo_count}"))
    except Exception as e:
        checks.append(("База данных", False, f"❌ {e}"))
        
    # Выводим результаты
    for name, status, icon in checks:
        print(f"{icon} {name}: {status}")
        
    # Итог
    failed = sum(1 for _, status, icon in checks if "❌" in str(icon))
    if failed == 0:
        print(f"\n🎉 Все системы готовы к работе!")
    else:
        print(f"\n⚠️  Найдено {failed} проблем - требует исправления")

if __name__ == "__main__":
    quick_status_check()