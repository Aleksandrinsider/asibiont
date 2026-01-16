"""
Утилиты для логирования изменений подписок в payment_history
"""
import logging
from models import Session, PaymentHistory, SubscriptionTier
from datetime import datetime, timezone
import json

logger = logging.getLogger(__name__)


def log_subscription_change(
    user_id: int,
    telegram_username: str,
    action: str,
    tier: SubscriptionTier,
    amount: str = None,
    payment_id: str = None,
    duration_days: int = None,
    start_date: datetime = None,
    end_date: datetime = None,
    details: dict = None
):
    """
    Логирует изменение подписки в payment_history
    
    Args:
        user_id: ID пользователя
        telegram_username: Telegram username
        action: Тип действия (payment, tier_change, subscription_activated, promo_used, manual_change, etc.)
        tier: Тариф подписки
        amount: Сумма платежа (если применимо)
        payment_id: ID платежа из внешней системы
        duration_days: Длительность подписки в днях
        start_date: Дата начала подписки
        end_date: Дата окончания подписки
        details: Дополнительные детали в виде словаря
    """
    session = Session()
    try:
        history_entry = PaymentHistory(
            user_id=user_id,
            telegram_username=telegram_username,
            action=action,
            tier=tier,
            amount=amount,
            payment_id=payment_id,
            duration_days=duration_days,
            start_date=start_date,
            end_date=end_date,
            details=json.dumps(details) if details else None,
            created_at=datetime.now(timezone.utc)
        )
        
        session.add(history_entry)
        session.commit()
        
        logger.info(
            f"📝 Логирование подписки: user={telegram_username} (ID={user_id}), "
            f"action={action}, tier={tier.value}"
        )
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка при логировании изменения подписки: {e}")
        session.rollback()
        return False
    finally:
        session.close()


def get_user_payment_history(user_id: int, limit: int = 10):
    """
    Получает историю платежей и изменений подписки пользователя
    
    Args:
        user_id: ID пользователя
        limit: Максимальное количество записей
        
    Returns:
        List of PaymentHistory objects
    """
    session = Session()
    try:
        history = session.query(PaymentHistory).filter_by(
            user_id=user_id
        ).order_by(
            PaymentHistory.created_at.desc()
        ).limit(limit).all()
        
        return history
        
    except Exception as e:
        logger.error(f"❌ Ошибка при получении истории платежей: {e}")
        return []
    finally:
        session.close()


def get_latest_active_subscription_from_history(user_id: int):
    """
    Получает последнюю активную подписку из истории
    Используется для восстановления данных в случае сброса
    
    Args:
        user_id: ID пользователя
        
    Returns:
        dict with tier, end_date or None
    """
    session = Session()
    try:
        now = datetime.now(timezone.utc)
        
        # Ищем последнюю запись о подписке, которая еще активна
        latest = session.query(PaymentHistory).filter(
            PaymentHistory.user_id == user_id,
            PaymentHistory.action.in_(['payment', 'subscription_activated', 'tier_change', 'promo_used']),
            PaymentHistory.end_date > now  # Подписка еще не истекла
        ).order_by(
            PaymentHistory.created_at.desc()
        ).first()
        
        if latest:
            return {
                'tier': latest.tier,
                'end_date': latest.end_date,
                'start_date': latest.start_date,
                'action': latest.action
            }
        
        return None
        
    except Exception as e:
        logger.error(f"❌ Ошибка при поиске активной подписки в истории: {e}")
        return None
    finally:
        session.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    
    # Пример использования
    from models import User
    
    session = Session()
    user = session.query(User).filter_by(username='sportfan3').first()
    
    if user:
        print(f"\n=== История платежей для {user.username} ===")
        history = get_user_payment_history(user.id)
        for entry in history:
            print(f"{entry.created_at}: {entry.action} - {entry.tier.value}")
        
        latest = get_latest_active_subscription_from_history(user.id)
        if latest:
            print(f"\n✓ Активная подписка: {latest['tier'].value} до {latest['end_date']}")
        else:
            print("\n✗ Активных подписок в истории не найдено")
    
    session.close()
