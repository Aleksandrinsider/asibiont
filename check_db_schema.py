from models import engine, Base
from sqlalchemy import inspect

# Inspect the database schema
inspector = inspect(engine)

print("Tables in database:")
tables = inspector.get_table_names()
print(tables)

for table_name in tables:
    print(f"\nColumns in {table_name}:")
    columns = inspector.get_columns(table_name)
    for col in columns:
        print(f"  {col['name']}: {col['type']} (nullable: {col['nullable']})")

# Check if models match
print("\nChecking model definitions:")
from models import User, Task, UserProfile
print("User columns:", [c.name for c in User.__table__.columns])
print("Task columns:", [c.name for c in Task.__table__.columns])
print("UserProfile columns:", [c.name for c in UserProfile.__table__.columns])