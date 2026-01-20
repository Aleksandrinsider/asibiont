"""
Скрипт для очистки всех таблиц базы данных
Используйте осторожно! Это удалит все данные.
"""
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from config import DATABASE_URL
from models import Base

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def clear_database():
    """Очистить все таблицы в базе данных"""
    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    try:
        logger.info("Начинаем очистку базы данных...")
        
        # Получаем все таблицы
        tables = Base.metadata.tables.keys()
        logger.info(f"Найдено таблиц: {list(tables)}")
        
        # Отключаем проверку внешних ключей (для PostgreSQL)
        if 'postgresql' in DATABASE_URL:
            session.execute(text("SET session_replication_role = 'replica';"))
        
        # Удаляем данные из всех таблиц
        for table_name in tables:
            logger.info(f"Очистка таблицы: {table_name}")
            session.execute(text(f"DELETE FROM {table_name}"))
        
        # Включаем обратно проверку внешних ключей
        if 'postgresql' in DATABASE_URL:
            session.execute(text("SET session_replication_role = 'origin';"))
        
        session.commit()
        logger.info("✅ База данных успешно очищена!")
        
    except Exception as e:
        session.rollback()
        logger.error(f"❌ Ошибка при очистке базы данных: {e}")
        raise
    finally:
        session.close()

if __name__ == "__main__":
    confirmation = input("⚠️  ВНИМАНИЕ! Это удалит ВСЕ данные из базы данных. Продолжить? (yes/no): ")
    if confirmation.lower() == 'yes':
        clear_database()
    else:
        logger.info("Операция отменена")
