"""
Script to completely clear production database - tasks, subscriptions, profiles, partners.
Keeps users table for authentication.
Set DATABASE_URL environment variable to connect to Railway PostgreSQL.

Usage:
    $env:DATABASE_URL='postgresql://...' ; python clear_production_db.py

Or simply visit with admin secret:
    https://task-production-31b6.up.railway.app/clear_database?secret=YOUR_ADMIN_SECRET
"""
import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Get DATABASE_URL from Railway environment
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL environment variable not set")
    print("Get it from Railway project variables")
    exit(1)

print(f"Connecting to: {DATABASE_URL[:30]}...")

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
db = Session()

try:
    # Check current state
    print("\n=== CURRENT DATABASE STATE ===")
    
    result = db.execute(text("SELECT COUNT(*) FROM tasks"))
    tasks_count = result.scalar()
    print(f"Tasks: {tasks_count}")
    
    result = db.execute(text("SELECT COUNT(*) FROM subscriptions"))
    subs_count = result.scalar()
    print(f"Subscriptions: {subs_count}")
    
    result = db.execute(text("SELECT COUNT(*) FROM user_profiles"))
    profiles_count = result.scalar()
    print(f"Profiles: {profiles_count}")
    
    result = db.execute(text("SELECT COUNT(*) FROM partners"))
    partners_count = result.scalar()
    print(f"Partners: {partners_count}")
    
    result = db.execute(text("SELECT COUNT(*) FROM users"))
    users_count = result.scalar()
    print(f"Users: {users_count}")
    
    # Confirm deletion
    print("\n⚠️  WARNING: This will delete ALL data (except users table)")
    confirm = input("Type 'DELETE ALL' to confirm: ")
    
    if confirm != "DELETE ALL":
        print("Cancelled")
        exit(0)
    
    print("\n🗑️  Deleting data...")
    
    # Delete in correct order (respect foreign keys)
    db.execute(text("DELETE FROM partners"))
    print("✓ Partners deleted")
    
    db.execute(text("DELETE FROM tasks"))
    print("✓ Tasks deleted")
    
    db.execute(text("DELETE FROM subscriptions"))
    print("✓ Subscriptions deleted")
    
    db.execute(text("DELETE FROM user_profiles"))
    print("✓ Profiles deleted")
    
    db.commit()
    print("\n✅ Database cleared successfully!")
    print("Users table preserved for authentication.")

except Exception as e:
    db.rollback()
    print(f"\n❌ ERROR: {e}")
finally:
    db.close()
