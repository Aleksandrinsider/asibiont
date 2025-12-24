from models import engine, Base
from sqlalchemy import inspect

# Inspect the database schema
inspector = inspect(engine)

print("Tables in database:")
tables = inspector.get_table_names()
print(tables)

for table_name in ['users', 'tasks']:
    if table_name in tables:
        print(f"\nColumns in {table_name}:")
        columns = inspector.get_columns(table_name)
        for col in columns:
            print(f"  {col['name']}: {col['type']} (nullable: {col['nullable']})")
    else:
        print(f"Table {table_name} does not exist")

# Check if models match
print("\nChecking model definitions:")
from models import User, Task
print("User columns:", [c.name for c in User.__table__.columns])
print("Task columns:", [c.name for c in Task.__table__.columns])