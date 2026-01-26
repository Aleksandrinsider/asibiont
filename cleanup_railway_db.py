#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Очистка Railway БД с сохранением промокодов и пользователя 146333757"""

import os
import sys

# Устанавливаем переменную окружения чтобы использовать Railway БД
os.environ['LOCAL'] = '0'

from models import Session, User, Task, UserProfile, Interaction, UserRating, Post, PostLike, Comment, PaymentHistory, Subscription
from sqlalchemy import text
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def cleanup_database():
    """Очистка всех таблиц кроме промокодов и пользователя 146333757"""
    try:
        session = Session()
        
        # Получить ID пользователя 146333757
        preserved_user = session.query(User).filter_by(telegram_id=146333757).first()
        preserved_user_id = preserved_user.id if preserved_user else None
        
        if preserved_user_id:
            logger.info(f"Сохраняем пользователя {preserved_user.telegram_id} (ID: {preserved_user_id})")
        else:
            logger.warning("Пользователь 146333757 не найден")
        
        # Удаление данных с условиями
        logger.info("Удаление комментариев...")
        if preserved_user_id:
            session.query(Comment).filter(Comment.user_id != preserved_user_id).delete(synchronize_session=False)
        else:
            session.query(Comment).delete(synchronize_session=False)
        
        logger.info("Удаление лайков постов...")
        if preserved_user_id:
            session.query(PostLike).filter(PostLike.user_id != preserved_user_id).delete(synchronize_session=False)
        else:
            session.query(PostLike).delete(synchronize_session=False)
        
        logger.info("Удаление постов...")
        if preserved_user_id:
            session.query(Post).filter(Post.user_id != preserved_user_id).delete(synchronize_session=False)
        else:
            session.query(Post).delete(synchronize_session=False)
        
        logger.info("Удаление рейтингов...")
        if preserved_user_id:
            session.query(UserRating).filter(
                (UserRating.rater_id != preserved_user_id) & 
                (UserRating.rated_user_id != preserved_user_id)
            ).delete(synchronize_session=False)
        else:
            session.query(UserRating).delete(synchronize_session=False)
        
        logger.info("Удаление взаимодействий...")
        if preserved_user_id:
            session.query(Interaction).filter(Interaction.user_id != preserved_user_id).delete(synchronize_session=False)
        else:
            session.query(Interaction).delete(synchronize_session=False)
        
        logger.info("Удаление задач...")
        if preserved_user_id:
            session.query(Task).filter(Task.user_id != preserved_user_id).delete(synchronize_session=False)
        else:
            session.query(Task).delete(synchronize_session=False)
        
        logger.info("Удаление истории платежей...")
        if preserved_user_id:
            session.query(PaymentHistory).filter(PaymentHistory.user_id != preserved_user_id).delete(synchronize_session=False)
        else:
            session.query(PaymentHistory).delete(synchronize_session=False)
        
        logger.info("Удаление подписок...")
        if preserved_user_id:
            session.query(Subscription).filter(Subscription.user_id != preserved_user_id).delete(synchronize_session=False)
        else:
            session.query(Subscription).delete(synchronize_session=False)
        
        logger.info("Удаление профилей пользователей...")
        if preserved_user_id:
            session.query(UserProfile).filter(UserProfile.user_id != preserved_user_id).delete(synchronize_session=False)
        else:
            session.query(UserProfile).delete(synchronize_session=False)
        
        logger.info("Удаление пользователей...")
        if preserved_user_id:
            deleted_users = session.query(User).filter(User.id != preserved_user_id).delete(synchronize_session=False)
            logger.info(f"Удалено пользователей: {deleted_users}")
        else:
            deleted_users = session.query(User).delete(synchronize_session=False)
            logger.info(f"Удалено пользователей: {deleted_users}")
        
        session.commit()
        logger.info("✅ БД успешно очищена! Промокоды и пользователь 146333757 сохранены.")
        
        # Показать оставшиеся данные
        users_count = session.query(User).count()
        promo_count = session.execute(text("SELECT COUNT(*) FROM promo_codes")).scalar()
        logger.info(f"Осталось пользователей: {users_count}")
        logger.info(f"Осталось промокодов: {promo_count}")
        
        session.close()
        
    except Exception as e:
        logger.error(f"❌ Ошибка при очистке БД: {e}", exc_info=True)
        session.rollback()
        session.close()
        sys.exit(1)

if __name__ == '__main__':
    confirm = input("⚠️  ВЫ СОБИРАЕТЕСЬ ОЧИСТИТЬ RAILWAY БД! Все данные кроме промокодов и пользователя 146333757 будут удалены.\nВведите 'YES' для подтверждения: ")
    if confirm == 'YES':
        cleanup_database()
    else:
        logger.info("Операция отменена")
