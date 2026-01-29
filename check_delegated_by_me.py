#!/usr/bin/env python3
"""
Check tasks delegated BY me (Поручил я) - verify delegated_to_username is set
"""
from models import Session, Task, User
from config import DATABASE_URL
import sys

def check_delegated_by_me():
    """Check tasks delegated by specific user"""
    session = Session()
    
    try:
        # Find user aleksandrinsider
        user = session.query(User).filter_by(username='aleksandrinsider').first()
        if not user:
            print("❌ User @aleksandrinsider not found")
            return
        
        print(f"✅ Found user: @{user.username} (ID: {user.id}, Telegram ID: {user.telegram_id})")
        print()
        
        # Find tasks delegated BY this user
        delegated_by_me = session.query(Task).filter(
            Task.delegated_by == user.id
        ).all()
        
        print(f"📋 Tasks delegated BY @{user.username}: {len(delegated_by_me)}")
        print()
        
        for task in delegated_by_me:
            print(f"Task ID: {task.id}")
            print(f"  Title: {task.title}")
            print(f"  Status: {task.status}")
            print(f"  Delegated By (ID): {task.delegated_by}")
            print(f"  Delegated To Username: {task.delegated_to_username}")
            print(f"  Delegation Status: {task.delegation_status}")
            print(f"  User ID (owner): {task.user_id}")
            
            # Check if owner is the same as delegator
            if task.user_id == user.id:
                print(f"  ✅ Owner matches delegator")
            else:
                owner = session.query(User).filter_by(id=task.user_id).first()
                print(f"  ⚠️ Owner is different: {owner.username if owner else 'unknown'}")
            
            print()
        
        # Show what API would return
        print("=" * 50)
        print("API Response Preview:")
        print("=" * 50)
        
        for task in delegated_by_me:
            delegated_by_me_flag = task.delegated_by == user.id
            print(f"Task {task.id}: '{task.title}'")
            print(f"  delegated_by_me: {delegated_by_me_flag}")
            print(f"  delegated_to_username: {task.delegated_to_username}")
            print(f"  Should show: Поручена @{task.delegated_to_username}")
            print()
            
    finally:
        session.close()

if __name__ == "__main__":
    try:
        check_delegated_by_me()
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
