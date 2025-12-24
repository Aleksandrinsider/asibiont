from models import engine, Base
from sqlalchemy import text

# Drop all tables and recreate to match models
with engine.connect() as conn:
    try:
        # Get all table names
        result = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public';"))
        tables = [row[0] for row in result]
        
        # Drop all tables
        for table in tables:
            conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
        
        conn.commit()
        print("All tables dropped")
        
        # Recreate tables from models
        Base.metadata.create_all(engine)
        print("Tables recreated from models")
        
    except Exception as e:
        print(f"Error: {e}")