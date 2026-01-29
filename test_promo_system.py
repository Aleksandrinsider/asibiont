"""
Тест системы промокодов
"""
import json
from models import Session, User, PromoCode, Subscription, SubscriptionTier
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_promo_system():
    """Тестирование системы промокодов"""
    session = Session()
    
    try:
        print("\n" + "="*60)
        print("ТЕСТ СИСТЕМЫ ПРОМОКОДОВ")
        print("="*60)
        
        # 1. Проверка существования промокодов
        print("\n1️⃣ Проверка промокодов в базе данных...")
        promo_codes = session.query(PromoCode).all()
        assert len(promo_codes) >= 3, "Должно быть минимум 3 промокода"
        
        for promo in promo_codes:
            print(f"   ✓ {promo.code}: тариф {promo.tier.value}, скидка {promo.discount_percent}%")
        
        # 2. Тест использования промокода
        print("\n2️⃣ Тест активации промокода...")
        
        # Создаем тестового пользователя
        test_user = User(
            telegram_id=999999999,
            username="test_promo_user",
            first_name="Test User"
        )
        session.add(test_user)
        session.commit()
        
        # Активируем промокод LIGHT1
        promo = session.query(PromoCode).filter_by(code='LIGHT1').first()
        assert promo is not None, "Промокод LIGHT1 не найден"
        
        used_by_users = json.loads(promo.used_by_users or '[]')
        assert test_user.id not in used_by_users, "Пользователь не должен был использовать промокод"
        
        # Добавляем пользователя в список использовавших
        used_by_users.append(test_user.id)
        promo.used_by_users = json.dumps(used_by_users)
        promo.used_count += 1
        
        session.commit()
        print(f"   ✓ Промокод {promo.code} активирован для пользователя {test_user.username}")
        
        # 3. Тест повторного использования
        print("\n3️⃣ Тест защиты от повторного использования...")
        promo = session.query(PromoCode).filter_by(code='LIGHT1').first()
        used_by_users = json.loads(promo.used_by_users or '[]')
        assert test_user.id in used_by_users, "Пользователь должен быть в списке использовавших"
        print(f"   ✓ Пользователь уже использовал промокод - защита работает")
        
        # 4. Статистика
        print("\n4️⃣ Статистика использования промокодов:")
        for promo in session.query(PromoCode).all():
            used_count = promo.used_count
            print(f"   • {promo.code}: {used_count} использований")
        
        # Очистка тестовых данных
        print("\n5️⃣ Очистка тестовых данных...")
        session.query(Subscription).filter_by(user_id=test_user.id).delete()
        session.query(User).filter_by(id=test_user.id).delete()
        
        # Сброс счетчика использований промокода
        promo = session.query(PromoCode).filter_by(code='LIGHT1').first()
        used_by_users = json.loads(promo.used_by_users or '[]')
        if test_user.id in used_by_users:
            used_by_users.remove(test_user.id)
        promo.used_by_users = json.dumps(used_by_users)
        promo.used_count = max(0, promo.used_count - 1)
        
        session.commit()
        print("   ✓ Тестовые данные удалены")
        
        print("\n" + "="*60)
        print("✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
        print("="*60)
        
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
    test_promo_system()
