"""
Script to add company and position fields to user_profiles table.
Миграция для добавления полей компании и должности.
Работает как с PostgreSQL, так и с SQLite.
"""
import os
os.environ.setdefault('LOCAL', '1')  # По умолчанию используем локальную БД

from models import Base, engine
from sqlalchemy import inspect, text
import sys

def add_company_position_fields():
    """Add company and position columns to user_profiles table"""
    inspector = inspect(engine)
    
    # Проверяем, существует ли таблица
    if 'user_profiles' not in inspector.get_table_names():
        print("Table 'user_profiles' does not exist. Creating all tables...")
        Base.metadata.create_all(engine)
        print("✓ All tables created successfully!")
        return
    
    # Получаем существующие колонки
    columns = [col['name'] for col in inspector.get_columns('user_profiles')]
    print(f"Existing columns: {', '.join(columns)}")
    
    # Определяем тип БД
    db_type = engine.dialect.name
    
    try:
        with engine.connect() as conn:
            # Добавляем колонку company, если её нет
            if 'company' not in columns:
                print("Adding 'company' column...")
                if db_type == 'sqlite':
                    conn.execute(text("ALTER TABLE user_profiles ADD COLUMN company VARCHAR(255)"))
                else:  # PostgreSQL
                    conn.execute(text("ALTER TABLE user_profiles ADD COLUMN company VARCHAR(255)"))
                conn.commit()
                print("✓ 'company' column added successfully")
            else:
                print("'company' column already exists")
            
            # Добавляем колонку position, если её нет
            if 'position' not in columns:
                print("Adding 'position' column...")
                if db_type == 'sqlite':
                    conn.execute(text("ALTER TABLE user_profiles ADD COLUMN position VARCHAR(255)"))
                else:  # PostgreSQL
                    conn.execute(text("ALTER TABLE user_profiles ADD COLUMN position VARCHAR(255)"))
                conn.commit()
                print("✓ 'position' column added successfully")
            else:
                print("'position' column already exists")
            
            print(f"\n✓ Migration completed successfully for {db_type}!")
            
    except Exception as e:
        print(f"✗ Error during migration: {e}")
        print(f"Database type: {db_type}")
        sys.exit(1)
    finally:
        engine.dispose()

if __name__ == "__main__":
    print("Starting migration: adding company and position fields...")
    print(f"Using database: {'SQLite (local)' if os.getenv('LOCAL') else 'PostgreSQL (Railway)'}")
    add_company_position_fields()
