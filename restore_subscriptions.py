"""
Скрипт для восстановления subscription_tier пользователей из таблицы subscriptions.
Запускается автоматически при старте приложения.
"""
import logging
from models import Session, User, Subscription, SubscriptionTier, PaymentHistory
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

def restore_user_tiers():
    """
    Восстанавливает subscription_tier пользователей из активных подписок.
    Если у пользователя есть активная подписка с определенным tier, 
    но user.subscription_tier отличается - синхронизирует их.
    
    Также проверяет payment_history как дополнительный источник данных.
    """
    session = Session()
    try:
        logger.info("Начинаем восстановление тарифов пользователей из подписок...")
        
        restored_count = 0
        now = datetime.now(timezone.utc)
        
        # Шаг 1: Восстановление из payment_history (более надежный источник)
        logger.info("Проверка payment_history...")
        try:
            all_users = session.query(User).all()
            for user in all_users:
                # Находим последнюю активную подписку в истории
                latest_payment = session.query(PaymentHistory).filter(
                    PaymentHistory.user_id == user.id,
                    PaymentHistory.action.in_(['payment', 'subscription_activated', 'tier_change', 'promo_used']),
                    PaymentHistory.end_date > now
                ).order_by(PaymentHistory.created_at.desc()).first()
                
                if latest_payment and user.subscription_tier != latest_payment.tier:
                    old_tier = user.subscription_tier
                    user.subscription_tier = latest_payment.tier
                    restored_count += 1
                    logger.info(
                        f"✓ [payment_history] Восстановлен тариф для {user.username or user.telegram_id}: "
                        f"{old_tier.value if old_tier else 'None'} -> {latest_payment.tier.value} "
                        f"(активен до {latest_payment.end_date})"
                    )
        except Exception as e:
            logger.warning(f"⚠️ Ошибка при восстановлении из payment_history (таблица может не существовать): {e}")
        
        # Шаг 2: Восстановление из subscriptions (основной механизм)
        logger.info("Проверка subscriptions...")
        active_subscriptions = session.query(Subscription).filter(
            Subscription.status == 'active'
        ).all()
        
        for subscription in active_subscriptions:
            # Проверяем, не истекла ли подписка (с учетом timezone)
            if subscription.end_date:
                end_date = subscription.end_date
                # Если end_date offset-naive, делаем его offset-aware
                if end_date.tzinfo is None:
                    end_date = end_date.replace(tzinfo=timezone.utc)
                if end_date < now:
                    logger.info(f"Подписка пользователя ID={subscription.user_id} истекла, пропускаем")
                    continue
            
            user = session.query(User).filter_by(id=subscription.user_id).first()
            if not user:
                logger.warning(f"Пользователь с ID={subscription.user_id} не найден для подписки ID={subscription.id}")
                continue
            
            # Если тариф пользователя не соответствует тарифу подписки - восстанавливаем
            if user.subscription_tier != subscription.tier:
                old_tier = user.subscription_tier
                user.subscription_tier = subscription.tier
                restored_count += 1
                logger.info(
                    f"✓ [subscriptions] Восстановлен тариф для пользователя {user.username or user.telegram_id}: "
                    f"{old_tier.value if old_tier else 'None'} -> {subscription.tier.value}"
                )
        
        if restored_count > 0:
            session.commit()
            logger.info(f"✅ Восстановлено тарифов: {restored_count}")
        else:
            logger.info("✓ Все тарифы пользователей соответствуют активным подпискам")
        
        return restored_count
        
    except Exception as e:
        logger.error(f"❌ Ошибка при восстановлении тарифов: {e}")
        session.rollback()
        return 0
    finally:
        session.close()


def check_database_integrity():
    """
    Проверяет целостность данных: есть ли пользователи с активными подписками,
    но с неправильным tier.
    """
    session = Session()
    try:
        inconsistencies = []
        now = datetime.now(timezone.utc)
        
        active_subscriptions = session.query(Subscription).filter(
            Subscription.status == 'active'
        ).all()
        
        for subscription in active_subscriptions:
            # Пропускаем истекшие подписки (с учетом timezone)
            if subscription.end_date:
                end_date = subscription.end_date
                # Если end_date offset-naive, делаем его offset-aware
                if end_date.tzinfo is None:
                    end_date = end_date.replace(tzinfo=timezone.utc)
                if end_date < now:
                    continue
                
            user = session.query(User).filter_by(id=subscription.user_id).first()
            if user and user.subscription_tier != subscription.tier:
                inconsistencies.append({
                    'user_id': user.id,
                    'username': user.username,
                    'user_tier': user.subscription_tier.value if user.subscription_tier else None,
                    'subscription_tier': subscription.tier.value,
                    'end_date': subscription.end_date
                })
        
        if inconsistencies:
            logger.warning(f"⚠️ Обнаружено несоответствий: {len(inconsistencies)}")
            for inc in inconsistencies:
                logger.warning(
                    f"  - Пользователь {inc['username']} (ID={inc['user_id']}): "
                    f"user.tier={inc['user_tier']}, subscription.tier={inc['subscription_tier']}"
                )
        else:
            logger.info("✓ Целостность данных подписок в порядке")
        
        return inconsistencies
        
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке целостности: {e}")
        return []
    finally:
        session.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    logger.info("=== Запуск проверки и восстановления подписок ===")
    
    # Сначала проверяем
    inconsistencies = check_database_integrity()
    
    # Если есть несоответствия - восстанавливаем
    if inconsistencies:
        restored = restore_user_tiers()
        logger.info(f"Операция завершена. Восстановлено: {restored}")
    else:
        logger.info("Восстановление не требуется")
