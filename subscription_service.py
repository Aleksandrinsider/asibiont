from models import Session, Subscription, User, PaymentHistory, SubscriptionTier
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
    """Создает платеж для месячной подписки"""
    from payments import get_tier_price
    amount = get_tier_price(tier)
    description = f"Подписка ASI Biont на месяц - {tier.capitalize()}"
    return create_payment(amount, description, user_id, tier)

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

def activate_subscription(user_id, plan='monthly', tier='light'):
    """Активирует подписку для пользователя"""
    session = Session()
    try:
        # First find the user by telegram_id
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return False, "Пользователь не найден"
        
        # Определяем tier enum
        tier_enum = SubscriptionTier.LIGHT
        if tier == 'standard':
            tier_enum = SubscriptionTier.STANDARD
        elif tier == 'premium':
            tier_enum = SubscriptionTier.PREMIUM
        
        # Check if subscription already exists
        sub = session.query(Subscription).filter_by(user_id=user.id).first()
        
        # Calculate dates
        start_date = datetime.datetime.now(pytz.UTC)
        duration_days = 30 if plan == 'monthly' else 365
        end_date = start_date + datetime.timedelta(days=duration_days)
        
        if sub:
            # Update existing subscription
            sub.status = 'active'
            sub.plan = plan
            sub.tier = tier_enum
            sub.start_date = start_date
            sub.end_date = end_date
            sub.telegram_id = user.telegram_id  # Update telegram_id
            sub.username = user.username  # Update username
            user.subscription_tier = tier_enum
            
            # Log to payment_history in same transaction
            try:
                payment_history = PaymentHistory(
                    user_id=user.id,
                    telegram_username=user.username,
                    action='subscription_activated',
                    tier=tier_enum,
                    duration_days=duration_days,
                    start_date=start_date,
                    end_date=end_date,
                    details=json.dumps({'plan': plan, 'method': 'activate_subscription'})
                )
                session.add(payment_history)
            except Exception as e:
                logger.error(f"❌ Failed to create payment history record: {e}")
            
            session.commit()
            logger.info(f"💾 Subscription activated: user={user.username}, tier={tier}")
            
            return True, f"Подписка обновлена до {sub.end_date.strftime('%d.%m.%Y')}"
        else:
            # Create new subscription
            new_sub = Subscription(
                user_id=user.id,
                telegram_id=user.telegram_id,  # Add telegram_id
                telegram_username=user.username,
                username=user.username,  # Add username
                status='active',
                plan=plan,
                tier=tier_enum,
                start_date=start_date,
                end_date=end_date,
                login_count=0
            )
            user.subscription_tier = tier_enum
            session.add(new_sub)
            session.commit()
            
            # Log to payment_history
            try:
                payment_history = PaymentHistory(
                    user_id=user.id,
                    telegram_username=user.username,
                    action='subscription_activated',
                    tier=tier_enum,
                    duration_days=duration_days,
                    start_date=start_date,
                    end_date=end_date,
                    details=json.dumps({'plan': plan, 'method': 'activate_subscription'})
                )
                session.add(payment_history)
                session.commit()
                logger.info(f"💾 New subscription logged: user={user.username}, tier={tier}")
            except Exception as e:
                logger.error(f"❌ Failed to log new subscription: {e}")
                session.rollback()
            
            return True, f"Подписка активирована до {end_date.strftime('%d.%m.%Y')}"
    except Exception as e:
        logger.error(f"Error activating subscription for user {user_id}: {e}")
        session.rollback()
        return False, f"Ошибка активации подписки: {str(e)}"
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
            if user.subscription_tier and user.subscription_tier != SubscriptionTier.LIGHT:
                return {
                    'status': 'active',
                    'plan': 'manual',
                    'tier': user.subscription_tier.value,
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
