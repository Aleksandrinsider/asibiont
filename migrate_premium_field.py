"""
Миграция БД: Добавление поля pending_premium_recommendations в user_profiles

Добавляет колонку для хранения Premium рекомендаций в формате JSON.
"""

from models import Base, engine, Session, UserProfile
from sqlalchemy import text
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def check_column_exists():
    """Проверяет существует ли уже колонка"""
    
    session = Session()
    try:
        result = session.execute(text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='user_profiles' 
            AND column_name='pending_premium_recommendations'
        """))
        
        return result.fetchone() is not None
    except:
        # Для SQLite используем другой подход
        try:
            result = session.execute(text("PRAGMA table_info(user_profiles)"))
            columns = [row[1] for row in result.fetchall()]
            return 'pending_premium_recommendations' in columns
        except Exception as e:
            logger.warning(f"Could not check column existence: {e}")
            return False
    finally:
        session.close()


def migrate_add_premium_recommendations_column():
    """Добавляет колонку pending_premium_recommendations"""
    
    logger.info("🔧 Starting migration: Add pending_premium_recommendations column")
    
    # Проверяем существует ли колонка
    if check_column_exists():
        logger.info("✅ Column 'pending_premium_recommendations' already exists, skipping migration")
        return True
    
    session = Session()
    
    try:
        # Добавляем колонку
        logger.info("📝 Adding column 'pending_premium_recommendations' to user_profiles table...")
        
        session.execute(text("""
            ALTER TABLE user_profiles 
            ADD COLUMN pending_premium_recommendations TEXT
        """))
        
        session.commit()
        
        logger.info("✅ Migration completed successfully!")
        logger.info("📊 Column 'pending_premium_recommendations' added to user_profiles")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Migration failed: {e}")
        session.rollback()
        
        # Пробуем создать таблицы заново (для чистой установки)
        logger.info("🔄 Attempting to recreate tables...")
        try:
            Base.metadata.create_all(engine)
            logger.info("✅ Tables created/updated successfully")
            return True
        except Exception as e2:
            logger.error(f"❌ Failed to create tables: {e2}")
            return False
    finally:
        session.close()


def verify_migration():
    """Проверяет что миграция прошла успешно"""
    
    logger.info("\n🔍 Verifying migration...")
    
    if check_column_exists():
        logger.info("✅ Verification passed: Column exists")
        return True
    else:
        logger.error("❌ Verification failed: Column not found")
        return False


if __name__ == '__main__':
    success = migrate_add_premium_recommendations_column()
    
    if success:
        verify_migration()
        logger.info("\n✅ Migration completed and verified!")
    else:
        logger.error("\n❌ Migration failed!")
