#!/usr/bin/env python3
"""
Миграция: добавление поля telegram_channel в таблицу users
"""
import sys
from sqlalchemy import text
from models import Session, engine
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_migration():
    """Добавить telegram_channel в users если его еще нет"""
    session = Session()
    
    try:
        # Проверяем тип БД
        db_type = engine.dialect.name
        logger.info(f"Тип БД: {db_type}")
        
        # Проверим, существует ли уже колонка
        if db_type == 'postgresql':
            result = session.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='users' AND column_name='telegram_channel'"
            ))
            exists = result.fetchone() is not None
        else:  # SQLite
            result = session.execute(text("PRAGMA table_info(users)"))
            columns = [row[1] for row in result.fetchall()]
            exists = 'telegram_channel' in columns
        
        if exists:
            logger.info("✅ Колонка telegram_channel уже существует")
            return True
        
        # Добавляем колонку
        logger.info("📝 Добавляем колонку telegram_channel...")
        session.execute(text(
            "ALTER TABLE users ADD COLUMN telegram_channel VARCHAR(255)"
        ))
        session.commit()
        
        logger.info("✅ Миграция успешно выполнена!")
        logger.info("   Добавлена колонка: users.telegram_channel")
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка миграции: {e}")
        session.rollback()
        return False
    finally:
        session.close()


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("МИГРАЦИЯ: Добавление telegram_channel")
    logger.info("=" * 60)
    
    success = run_migration()
    
    if success:
        logger.info("\n✅ Миграция завершена успешно")
        sys.exit(0)
    else:
        logger.error("\n❌ Миграция провалилась")
        sys.exit(1)
