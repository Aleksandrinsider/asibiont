"""Миграция для изменения типа telegram_id с Integer на BigInteger"""
import sqlite3
import os
import logging
from sqlalchemy import create_engine, text
from models import Base, User, Task, UserProfile
from config import DATABASE_URL

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def migrate_telegram_id_to_bigint():
    """Изменяет тип поля telegram_id с Integer на BigInteger"""
    
    if 'sqlite' in DATABASE_URL.lower():
        logger.info("SQLite обнаружен - делаем полную пересборку базы")
        
        # Для SQLite - создаем новую базу с правильными типами
        if os.path.exists('database.db'):
            os.rename('database.db', 'database_backup.db')
            logger.info("Создан бэкап database.db -> database_backup.db")
        
        # Создаем новую базу
        engine = create_engine(DATABASE_URL, echo=True)
        Base.metadata.create_all(engine)
        logger.info("Новая база создана с BigInteger для telegram_id")
        
    else:
        logger.info("PostgreSQL - делаем ALTER TABLE")
        engine = create_engine(DATABASE_URL, echo=True)
        
        with engine.connect() as conn:
            # Изменяем тип поля telegram_id на BIGINT
            conn.execute(text("ALTER TABLE users ALTER COLUMN telegram_id TYPE BIGINT"))
            conn.commit()
            logger.info("Тип поля telegram_id изменен на BIGINT")


if __name__ == "__main__":
    migrate_telegram_id_to_bigint()