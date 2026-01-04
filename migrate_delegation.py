"""
Migration script to add delegation fields to tasks table
"""
from sqlalchemy import create_engine, text
from config import DATABASE_URL

def migrate():
    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        try:
            # Add delegated_by column
            conn.execute(text("ALTER TABLE tasks ADD COLUMN delegated_by INTEGER"))
            print("Added delegated_by column")
        except Exception as e:
            print(f"delegated_by already exists or error: {e}")
        
        try:
            # Add delegated_to_username column
            conn.execute(text("ALTER TABLE tasks ADD COLUMN delegated_to_username VARCHAR(255)"))
            print("Added delegated_to_username column")
        except Exception as e:
            print(f"delegated_to_username already exists or error: {e}")
        
        try:
            # Add delegation_status column
            conn.execute(text("ALTER TABLE tasks ADD COLUMN delegation_status VARCHAR(50)"))
            print("Added delegation_status column")
        except Exception as e:
            print(f"delegation_status already exists or error: {e}")
        
        try:
            # Add delegation_details column
            conn.execute(text("ALTER TABLE tasks ADD COLUMN delegation_details TEXT"))
            print("Added delegation_details column")
        except Exception as e:
            print(f"delegation_details already exists or error: {e}")
        
        conn.commit()
        print("Migration completed successfully!")

if __name__ == "__main__":
    migrate()
