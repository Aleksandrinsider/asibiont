"""
Backup database script for Railway deployment
Run this before major deploys to save data
"""
import json
from datetime import datetime
from models import Session, User, Task, UserProfile, Interaction

def backup_database():
    session = Session()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    try:
        # Backup users
        users = session.query(User).all()
        users_data = []
        for user in users:
            users_data.append({
                'telegram_id': user.telegram_id,
                'username': user.username,
                'first_name': user.first_name,
                'timezone': user.timezone,
                'memory': user.memory
            })
        
        # Backup tasks
        tasks = session.query(Task).all()
        tasks_data = []
        for task in tasks:
            tasks_data.append({
                'user_telegram_id': session.query(User).filter_by(id=task.user_id).first().telegram_id,
                'title': task.title,
                'description': task.description,
                'status': task.status,
                'reminder_time': task.reminder_time.isoformat() if task.reminder_time else None,
                'due_date': task.due_date.isoformat() if task.due_date else None,
                'delegated_to_username': task.delegated_to_username,
                'delegation_status': task.delegation_status
            })
        
        # Backup profiles
        profiles = session.query(UserProfile).all()
        profiles_data = []
        for profile in profiles:
            profiles_data.append({
                'user_telegram_id': session.query(User).filter_by(id=profile.user_id).first().telegram_id,
                'city': profile.city,
                'company': profile.company,
                'position': profile.position,
                'skills': profile.skills,
                'interests': profile.interests,
                'goals': profile.goals
            })
        
        backup_data = {
            'timestamp': timestamp,
            'users_count': len(users_data),
            'tasks_count': len(tasks_data),
            'profiles_count': len(profiles_data),
            'users': users_data,
            'tasks': tasks_data,
            'profiles': profiles_data
        }
        
        filename = f'backup_{timestamp}.json'
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, ensure_ascii=False, indent=2)
        
        print(f"✅ Backup created: {filename}")
        print(f"   Users: {len(users_data)}")
        print(f"   Tasks: {len(tasks_data)}")
        print(f"   Profiles: {len(profiles_data)}")
        
    except Exception as e:
        print(f"❌ Backup failed: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    print("=" * 60)
    print("DATABASE BACKUP")
    print("=" * 60)
    backup_database()
