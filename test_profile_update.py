"""
Тестовый скрипт для проверки обновления профиля и отображения в панели
"""
import asyncio
from models import Session, User, UserProfile
from ai_integration.handlers import update_profile
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_profile_update():
    """Тест обновления профиля"""
    
    # Используем тестовый user_id (замените на реальный)
    test_user_id = 384303286  # Ваш telegram_id
    
    session = Session()
    try:
        # Проверяем текущее состояние профиля
        user = session.query(User).filter_by(telegram_id=test_user_id).first()
        if not user:
            logger.error(f"User {test_user_id} not found")
            return
        
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            logger.error(f"Profile for user {user.id} not found")
            return
        
        logger.info(f"\n=== BEFORE UPDATE ===")
        logger.info(f"Interests: {profile.interests}")
        logger.info(f"Skills: {profile.skills}")
        logger.info(f"Goals: {profile.goals}")
        
        # Тестируем обновление интересов
        result = update_profile(
            interests="спорт",  # Добавляем спорт
            user_id=test_user_id,
            session=session
        )
        
        logger.info(f"\n=== UPDATE RESULT ===")
        logger.info(f"Result: {result}")
        
        # Проверяем что изменилось
        session.refresh(profile)
        logger.info(f"\n=== AFTER UPDATE ===")
        logger.info(f"Interests: {profile.interests}")
        logger.info(f"Skills: {profile.skills}")
        logger.info(f"Goals: {profile.goals}")
        
        # Проверяем кеш Redis
        try:
            from ai_integration.utils import redis_client
            if redis_client:
                cache_key = f"profile:{test_user_id}"
                cached_data = await redis_client.get(cache_key)
                if cached_data:
                    logger.warning(f"\n=== CACHE STILL EXISTS ===")
                    logger.warning(f"Cache should be deleted but still present!")
                else:
                    logger.info(f"\n=== CACHE INVALIDATED ===")
                    logger.info(f"Cache successfully deleted for key: {cache_key}")
        except Exception as e:
            logger.warning(f"Could not check cache: {e}")
        
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_profile_update())
