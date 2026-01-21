"""
Скрипт для проверки конкретного пользователя и его общих интересов с другими
"""
import logging
from models import Session, User, UserProfile

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def check_common_interests(telegram_id):
    """Проверить общие интересы пользователя"""
    session = Session()
    try:
        # Найти пользователя
        user = session.query(User).filter_by(telegram_id=telegram_id).first()
        if not user:
            logger.error(f"Пользователь с telegram_id={telegram_id} не найден")
            return
        
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        
        logger.info(f"\n=== Ваш профиль ===")
        logger.info(f"User: {user.username or user.first_name}")
        if profile:
            logger.info(f"Интересы: {profile.interests or 'НЕ ЗАПОЛНЕНО'}")
            logger.info(f"Навыки: {profile.skills or 'НЕ ЗАПОЛНЕНО'}")
            logger.info(f"Цели: {profile.goals or 'НЕ ЗАПОЛНЕНО'}")
            
            if not profile.interests:
                logger.warning("\n⚠️ У ВАС НЕ ЗАПОЛНЕНЫ ИНТЕРЕСЫ! Поэтому не показываются общие интересы.")
                return
            
            # Проверяем общие интересы с другими пользователями
            user_interests = set(i.strip().lower() for i in profile.interests.split(','))
            logger.info(f"\nВаши интересы (нормализованные): {user_interests}")
            
            # Найти других пользователей со схожими интересами
            other_users = session.query(User).join(UserProfile).filter(
                User.id != user.id,
                UserProfile.interests.isnot(None)
            ).all()
            
            logger.info(f"\n=== Проверка общих интересов ===")
            for other_user in other_users[:10]:
                other_profile = session.query(UserProfile).filter_by(user_id=other_user.id).first()
                if other_profile and other_profile.interests:
                    other_interests = set(i.strip().lower() for i in other_profile.interests.split(','))
                    common = user_interests & other_interests
                    if common:
                        logger.info(f"\n✓ {other_user.username or other_user.first_name}:")
                        logger.info(f"  Интересы: {other_profile.interests}")
                        logger.info(f"  ОБЩИЕ: {', '.join(common)}")
        else:
            logger.error("Профиль НЕ СОЗДАН")
        
    except Exception as e:
        logger.error(f"Ошибка: {e}", exc_info=True)
    finally:
        session.close()

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        telegram_id = int(sys.argv[1])
    else:
        # По умолчанию проверяем тестового пользователя 1001
        telegram_id = 1001
    
    check_common_interests(telegram_id)
    logger.info("\nГотово!")
