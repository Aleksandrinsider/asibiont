"""
Production-safe migration script
Добавляет company и position только если их нет
"""
from sqlalchemy import create_engine, text, inspect
from config import DATABASE_URL
import sys

def migrate():
    """Безопасная миграция для production"""
    try:
        # Fix URL for PostgreSQL
        db_url = DATABASE_URL
        if db_url and db_url.startswith('postgresql://'):
            db_url = db_url.replace('postgresql://', 'postgresql+psycopg2://', 1)
        
        print(f"Connecting to database...")
        engine = create_engine(db_url, pool_pre_ping=True)
        
        # Проверяем существующие колонки
        inspector = inspect(engine)
        
        if 'user_profiles' not in inspector.get_table_names():
            print("Table user_profiles not found. Creating tables...")
            from models import Base
            Base.metadata.create_all(engine)
            print("✓ Tables created")
            return
        
        columns = {col['name'] for col in inspector.get_columns('user_profiles')}
        print(f"Existing columns: {columns}")
        
        with engine.begin() as conn:
            # Добавляем company
            if 'company' not in columns:
                print("Adding company column...")
                conn.execute(text("ALTER TABLE user_profiles ADD COLUMN company VARCHAR(255)"))
                print("✓ company added")
            else:
                print("• company already exists")
            
            # Добавляем position
            if 'position' not in columns:
                print("Adding position column...")
                conn.execute(text("ALTER TABLE user_profiles ADD COLUMN position VARCHAR(255)"))
                print("✓ position added")
            else:
                print("• position already exists")
        
        print("\n✓ Migration completed successfully!")
        
    except Exception as e:
        print(f"✗ Migration error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        engine.dispose()

if __name__ == "__main__":
    migrate()
