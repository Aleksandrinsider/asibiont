"""
Создание таблицы promo_codes в Railway
"""
from models import Base, engine
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    logger.info("Создание таблицы promo_codes...")
    Base.metadata.create_all(engine)
    logger.info("✓ Таблица promo_codes создана успешно!")
    
    # Проверяем таблицу
    from sqlalchemy import inspect
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    
    logger.info(f"\nВсе таблицы в базе данных ({len(tables)}):")
    for table in sorted(tables):
        logger.info(f"  - {table}")
    
    if 'promo_codes' in tables:
        logger.info("\n✅ Таблица promo_codes существует!")
        
        # Показываем колонки
        columns = inspector.get_columns('promo_codes')
        logger.info("\nКолонки таблицы promo_codes:")
        for col in columns:
            logger.info(f"  - {col['name']}: {col['type']}")
    else:
        logger.error("\n❌ Таблица promo_codes НЕ найдена!")
        
except Exception as e:
    logger.error(f"Ошибка: {e}")
    raise
