"""
Script to create a test delegated task in Railway database
"""
import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

# Get Railway database URL
DATABASE_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_PUBLIC_URL or DATABASE_URL not found in .env")
    exit(1)

print(f"Connecting to database...")
engine = create_engine(DATABASE_URL)

# Get user IDs
with engine.connect() as conn:
    # Get @aleksandrinsider user
    result = conn.execute(text("""
        SELECT id, username FROM users WHERE username = 'aleksandrinsider'
    """))
    aleksandr = result.fetchone()
    
    if not aleksandr:
        print("ERROR: User @aleksandrinsider not found")
        exit(1)
    
    # Get @football_dmitry user  
    result = conn.execute(text("""
        SELECT id, username FROM users WHERE username = 'football_dmitry'
    """))
    dmitry = result.fetchone()
    
    if not dmitry:
        print("ERROR: User @football_dmitry not found")
        exit(1)
    
    print(f"Found users:")
    print(f"  @aleksandrinsider (ID: {aleksandr[0]})")
    print(f"  @football_dmitry (ID: {dmitry[0]})")
    
    # Create delegated task
    reminder_time = datetime.now() + timedelta(days=3)
    
    conn.execute(text("""
        INSERT INTO tasks (
            user_id, 
            delegated_by, 
            delegated_to_username,
            title, 
            description, 
            reminder_time,
            status,
            delegation_status,
            created_at
        ) VALUES (
            :user_id,
            :delegated_by,
            :delegated_to_username,
            :title,
            :description,
            :reminder_time,
            'pending',
            'pending',
            :created_at
        )
    """), {
        'user_id': dmitry[0],  # Task belongs to dmitry
        'delegated_by': aleksandr[0],  # Delegated by aleksandr
        'delegated_to_username': 'football_dmitry',
        'title': 'Протестировать делегирование задач',
        'description': 'Проверить работу функции делегирования в разделе Поручил я',
        'reminder_time': reminder_time,
        'created_at': datetime.now()
    })
    
    conn.commit()
    
    print(f"\n✅ Task created successfully!")
    print(f"   Title: Протестировать делегирование задач")
    print(f"   Delegated by: @aleksandrinsider")
    print(f"   Delegated to: @football_dmitry")
    print(f"   Reminder: {reminder_time.strftime('%d.%m.%Y %H:%M')}")
    
    # Show all tasks for aleksandrinsider
    result = conn.execute(text("""
        SELECT 
            t.id,
            t.title,
            t.delegated_to_username,
            t.user_id,
            t.delegated_by
        FROM tasks t
        WHERE t.delegated_by = :user_id
        ORDER BY t.created_at DESC
        LIMIT 5
    """), {'user_id': aleksandr[0]})
    
    tasks = result.fetchall()
    print(f"\n📋 Tasks delegated by @aleksandrinsider:")
    for task in tasks:
        print(f"   - ID {task[0]}: {task[1]} → @{task[2]}")
