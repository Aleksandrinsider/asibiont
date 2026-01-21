"""
Скрипт для проверки заполненности профилей пользователей
"""
import logging
from models import Session, User, UserProfile

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def check_profiles():
    """Проверить профили пользователей"""
    session = Session()
    try:
        # Найти всех пользователей с профилями
        users = session.query(User).join(UserProfile, User.id == UserProfile.user_id, isouter=True).all()
        
        for user in users[:10]:  # Первые 10 пользователей
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            logger.info(f"\nUser: {user.username or user.first_name} (ID: {user.id})")
            logger.info(f"  Telegram ID: {user.telegram_id}")
            if profile:
                logger.info(f"  Интересы: {profile.interests or 'НЕ ЗАПОЛНЕНО'}")
                logger.info(f"  Навыки: {profile.skills or 'НЕ ЗАПОЛНЕНО'}")
                logger.info(f"  Цели: {profile.goals or 'НЕ ЗАПОЛНЕНО'}")
                logger.info(f"  Город: {profile.city or 'НЕ ЗАПОЛНЕНО'}")
            else:
                logger.info("  Профиль НЕ СОЗДАН")
        
    except Exception as e:
        logger.error(f"Ошибка при проверке профилей: {e}", exc_info=True)
    finally:
        session.close()

if __name__ == '__main__':
    check_profiles()
    logger.info("\nГотово!")
