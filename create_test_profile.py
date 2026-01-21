"""
Создание профиля для тестового пользователя
"""
import logging
from models import Session, User, UserProfile

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_profile_for_user(telegram_id):
    """Создать профиль для пользователя"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=telegram_id).first()
        if not user:
            logger.error(f"Пользователь с telegram_id={telegram_id} не найден")
            return
        
        # Проверить существующий профиль
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        
        if profile:
            logger.info(f"Обновление профиля для {user.username or user.first_name}")
            profile.interests = "спорт, бег, фитнес, здоровый образ жизни"
            profile.skills = "спортивные тренировки, мотивация, планирование"
            profile.goals = "улучшить физическую форму, пробежать марафон"
            profile.city = "Москва"
            profile.bio = "Люблю спорт и активный образ жизни"
        else:
            logger.info(f"Создание нового профиля для {user.username or user.first_name}")
            profile = UserProfile(
                user_id=user.id,
                contact_info=user.username,
                interests="спорт, бег, фитнес, здоровый образ жизни",
                skills="спортивные тренировки, мотивация, планирование",
                goals="улучшить физическую форму, пробежать марафон",
                city="Москва",
                bio="Люблю спорт и активный образ жизни"
            )
            session.add(profile)
        
        session.commit()
        logger.info("✓ Профиль успешно создан/обновлен!")
        logger.info(f"  Интересы: {profile.interests}")
        logger.info(f"  Навыки: {profile.skills}")
        logger.info(f"  Цели: {profile.goals}")
        logger.info(f"  Город: {profile.city}")
        
    except Exception as e:
        logger.error(f"Ошибка: {e}", exc_info=True)
        session.rollback()
    finally:
        session.close()

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        telegram_id = int(sys.argv[1])
    else:
        telegram_id = 34  # По умолчанию для пользователя 34
    
    create_profile_for_user(telegram_id)
    logger.info("\nГотово!")
