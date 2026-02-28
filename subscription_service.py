from models import Session, Subscription, User, PaymentHistory
import datetime
import pytz
from config import FREE_ACCESS_MODE
from payments import create_payment
import logging
import json

logger = logging.getLogger(__name__)

def check_subscription(user_id):
    """Проверяет доступ: FREE_ACCESS_MODE, активная подписка, или баланс токенов > 0."""
    if FREE_ACCESS_MODE:
        return True
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return False
        
        # Новая проверка: есть токены — есть доступ
        if (user.token_balance or 0) > 0:
            return True
        
        # Fallback: старые подписки (для обратной совместимости)
        sub = session.query(Subscription).filter_by(user_id=user.id).first()
        if sub and sub.status == 'active':
            if sub.end_date is None:
                return True
            now = datetime.datetime.now(pytz.UTC)
            if sub.end_date.tzinfo is None:
                now_naive = now.replace(tzinfo=None)
                return sub.end_date > now_naive
            else:
                return sub.end_date > now
            
        return False
    except Exception as e:
        logger.error(f"Error checking subscription for user {user_id}: {e}")
        return False
    finally:
        session.close()

def create_subscription_payment(user_id, tier='light'):
    """Создает платеж для покупки токенов (legacy, перенаправляет на токены)"""
    from payments import TOKEN_PACK_PRICES
    # Legacy: любой запрос на подписку превращаем в минимальный пакет токенов
    pack = TOKEN_PACK_PRICES.get('tokens_small', {'price': 1500, 'tokens': 1500})
    description = f"Пополнение {pack['tokens']} токенов"
    return create_payment(pack['price'], description, user_id, 'tokens_small')

def cancel_subscription(user_id):
    """Отменяет подписку пользователя"""
    session = Session()
    try:
        # First find the user by telegram_id
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return False
            
        sub = session.query(Subscription).filter_by(user_id=user.id).first()
        if sub:
            sub.status = 'cancelled'
            session.commit()
            return True
        return False
    except Exception as e:
        logger.error(f"Error cancelling subscription for user {user_id}: {e}")
        session.rollback()
        return False
    finally:
        session.close()

def get_subscription_status(user_id):
    """Получить статус подписки пользователя"""
    session = Session()
    try:
        # First find the user by telegram_id
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return None
        
        # Then find subscription by user.id
        sub = session.query(Subscription).filter_by(user_id=user.id).first()
        if sub:
            return {
                'status': sub.status,
                'plan': sub.plan,
                'tier': sub.tier.value if sub.tier else 'LIGHT',
                'start_date': sub.start_date.isoformat() if sub.start_date else None,
                'end_date': sub.end_date.isoformat() if sub.end_date else None,
                'login_count': sub.login_count
            }
        else:
            # Если нет записи в Subscription, но есть subscription_tier, используем его
            if user.subscription_tier and str(user.subscription_tier) != 'LIGHT':
                return {
                    'status': 'active',
                    'plan': 'manual',
                    'tier': str(user.subscription_tier.value) if hasattr(user.subscription_tier, 'value') else str(user.subscription_tier),
                    'start_date': None,
                    'end_date': None,
                    'login_count': 0
                }
            return None
    except Exception as e:
        logger.error(f"Error getting subscription status for user {user_id}: {e}")
        return None
    finally:
        session.close()
