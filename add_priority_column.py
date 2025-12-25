from models import engine
from sqlalchemy import text

# Add priority column to tasks table if it doesn't exist
with engine.connect() as conn:
    try:
        # Check if column exists (SQLite way)
        result = conn.execute(text("PRAGMA table_info(tasks)"))
        columns = [row[1] for row in result]
        if 'priority' not in columns:
            # Add the column
            conn.execute(text("ALTER TABLE tasks ADD COLUMN priority VARCHAR(20) DEFAULT 'medium'"))
            conn.commit()
            print("Added priority column to tasks table")
        else:
            print("Priority column already exists")
    except Exception as e:
        print(f"Error: {e}")