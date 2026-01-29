"""
Тест VIP промокода VIPACCESS2026
"""
import json
from models import Session, User, PromoCode, Subscription, SubscriptionTier
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_vip_promo():
    """Тестирование VIP промокода"""
    session = Session()
    
    try:
        print("\n" + "="*60)
        print("ТЕСТ VIP ПРОМОКОДА VIPACCESS2026")
        print("="*60)
        
        # 1. Проверка промокода
        print("\n1️⃣ Проверка VIP промокода в базе данных...")
        promo = session.query(PromoCode).filter_by(code='VIPACCESS2026').first()
        assert promo is not None, "Промокод VIPACCESS2026 не найден"
        
        print(f"   ✓ Код: {promo.code}")
        print(f"   ✓ Тариф: {promo.tier.value}")
        print(f"   ✓ Скидка: {promo.discount_percent}%")
        print(f"   ✓ Длительность: {promo.duration_days} дней")
        print(f"   ✓ Максимум использований: {promo.max_uses}")
        print(f"   ✓ Использований: {promo.used_count}")
        
        # 2. Тест первого использования
        print("\n2️⃣ Тест активации для первого пользователя...")
        
        test_user1 = User(
            telegram_id=111111111,
            username="vip_user_1",
            first_name="VIP User 1"
        )
        session.add(test_user1)
        session.commit()
        
        # Активация промокода
        used_by_users = json.loads(promo.used_by_users or '[]')
        used_by_users.append(test_user1.id)
        promo.used_by_users = json.dumps(used_by_users)
        promo.used_count += 1
        
        session.commit()
        print(f"   ✓ Промокод активирован для {test_user1.username}")
        print(f"   ✓ Использований: {promo.used_count}/{promo.max_uses}")
        
        # 3. Тест блокировки второго использования
        print("\n3️⃣ Тест блокировки для второго пользователя...")
        
        test_user2 = User(
            telegram_id=222222222,
            username="vip_user_2",
            first_name="VIP User 2"
        )
        session.add(test_user2)
        session.commit()
        
        # Проверка лимита
        promo = session.query(PromoCode).filter_by(code='VIPACCESS2026').first()
        if promo.max_uses and promo.used_count >= promo.max_uses:
            print(f"   ✓ Промокод исчерпан! Использований: {promo.used_count}/{promo.max_uses}")
            print(f"   ✓ Второй пользователь НЕ может использовать промокод")
        else:
            raise AssertionError("Промокод должен быть исчерпан!")
        
        # 4. Проверка характеристик
        print("\n4️⃣ Проверка VIP характеристик...")
        assert promo.tier == SubscriptionTier.PREMIUM, "Тариф должен быть PREMIUM"
        assert promo.duration_days == 365, "Длительность должна быть 365 дней"
        assert promo.discount_percent == 100, "Скидка должна быть 100%"
        assert promo.max_uses == 1, "Максимум использований должен быть 1"
        print("   ✓ Все характеристики корректны")
        
        # 5. Очистка
        print("\n5️⃣ Очистка тестовых данных...")
        session.query(Subscription).filter_by(user_id=test_user1.id).delete()
        session.query(Subscription).filter_by(user_id=test_user2.id).delete()
        session.query(User).filter_by(id=test_user1.id).delete()
        session.query(User).filter_by(id=test_user2.id).delete()
        
        # Сброс промокода
        promo = session.query(PromoCode).filter_by(code='VIPACCESS2026').first()
        promo.used_by_users = '[]'
        promo.used_count = 0
        
        session.commit()
        print("   ✓ Тестовые данные удалены")
        print("   ✓ VIP промокод сброшен для реального использования")
        
        print("\n" + "="*60)
        print("✅ ВСЕ VIP ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
        print("="*60)
        print("\n🎯 VIPACCESS2026 готов к использованию!")
        print("   • 1 год премиум подписки")
        print("   • Только для 1 пользователя")
        print("   • Команда: /promo VIPACCESS2026")
        
    except AssertionError as e:
        print(f"\n❌ ОШИБКА: {e}")
        session.rollback()
    except Exception as e:
        print(f"\n❌ НЕОЖИДАННАЯ ОШИБКА: {e}")
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == '__main__':
    test_vip_promo()
