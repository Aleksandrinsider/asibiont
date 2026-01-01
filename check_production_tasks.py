"""
Script to check and delete old tasks from production database.
Set environment variable PRODUCTION=1 to connect to Railway PostgreSQL.
"""
import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import pytz

# Get DATABASE_URL from Railway environment
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL environment variable not set")
    print("Copy it from Railway project variables")
    exit(1)

print(f"Connecting to: {DATABASE_URL[:30]}...")

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
db = Session()

try:
    # Get all tasks
    result = db.execute(text("""
        SELECT t.id, t.title, t.status, t.reminder_time, t.created_at, u.telegram_id, u.username 
        FROM tasks t 
        JOIN users u ON t.user_id = u.id 
        ORDER BY t.created_at DESC
    """))
    tasks = result.fetchall()

    print(f"\n=== TOTAL TASKS IN PRODUCTION: {len(tasks)} ===\n")
    
    old_tasks = []
    cutoff = datetime(2026, 1, 1, tzinfo=pytz.UTC)
    
    for task in tasks:
        print(f"ID: {task[0]}")
        print(f"  Title: {task[1]}")
        print(f"  Status: {task[2]}")
        print(f"  Reminder: {task[3]}")
        print(f"  User: {task[6]} (ID: {task[5]})")
        print(f"  Created: {task[4]}")
        
        if task[3] and task[3] < cutoff:
            old_tasks.append(task[0])
            print("  ⚠️ OLD TASK (before 2026-01-01)")
        print()
    
    if old_tasks:
        print(f"\n⚠️ Found {len(old_tasks)} old tasks to delete")
        print(f"Task IDs: {old_tasks}")
        print("\nTo delete these tasks, visit:")
        print("https://task-production-31b6.up.railway.app/clear_old_tasks?secret=YOUR_ADMIN_SECRET")
    else:
        print("✅ No old tasks found")

except Exception as e:
    print(f"ERROR: {e}")
finally:
    db.close()
