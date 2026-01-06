# -*- coding: utf-8 -*-
from models import Session, User, Task, UserProfile
from datetime import datetime, timedelta
import pytz

USER_ID = 146333757

session = Session()
try:
    user = session.query(User).filter_by(telegram_id=USER_ID).first()
    if not user:
        print(f"User {USER_ID} not found")
        exit(1)
    
    print(f"Setting up for @{user.username}...")
    
    # Update profile
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if not profile:
        profile = UserProfile(user_id=user.id)
        session.add(profile)
    
    profile.interests = "Python, AI, startups"
    profile.skills = "Product Management, AI/ML"
    profile.position = "Product Manager"
    profile.city = "Moscow"
    
    # Create contacts
    if not session.query(User).filter_by(username="testuser").first():
        c1 = User(telegram_id=111222333, username="testuser", first_name="Test User")
        session.add(c1)
        session.flush()
        session.add(UserProfile(user_id=c1.id, interests="Testing", position="QA"))
    
    if not session.query(User).filter_by(username="developer_alex").first():
        c2 = User(telegram_id=444555666, username="developer_alex", first_name="Alex Dev")
        session.add(c2)
        session.flush()
        session.add(UserProfile(user_id=c2.id, interests="Backend", position="Developer"))
    
    if not session.query(User).filter_by(username="designer_maria").first():
        c3 = User(telegram_id=777888999, username="designer_maria", first_name="Maria")
        session.add(c3)
        session.flush()
        session.add(UserProfile(user_id=c3.id, interests="UI/UX", position="Designer"))
    
    session.commit()
    
    # Get contacts
    c1 = session.query(User).filter_by(username="testuser").first()
    c2 = session.query(User).filter_by(username="developer_alex").first()
    c3 = session.query(User).filter_by(username="designer_maria").first()
    
    # Create delegation tasks
    t1 = Task(
        title="API endpoint",
        user_id=user.id,
        delegated_to_username=f"@{c2.username}",
        status="pending",
        reminder_time=datetime.now(pytz.UTC) + timedelta(days=3)
    )
    session.add(t1)
    
    t2 = Task(
        title="Design mockup",
        user_id=user.id,
        delegated_to_username=f"@{c3.username}",
        status="pending",
        reminder_time=datetime.now(pytz.UTC) + timedelta(days=2)
    )
    session.add(t2)
    
    t3 = Task(
        title="Review PR",
        user_id=c1.id,
        delegated_to_username=f"@{user.username}",
        status="pending",
        reminder_time=datetime.now(pytz.UTC) + timedelta(hours=6)
    )
    session.add(t3)
    
    t4 = Task(
        title="Test feature",
        user_id=c2.id,
        delegated_to_username=f"@{user.username}",
        status="pending",
        reminder_time=datetime.now(pytz.UTC) + timedelta(hours=12)
    )
    session.add(t4)
    
    session.commit()
    
    print("OK Created:")
    print("  - Profile updated")
    print("  - 3 contacts")
    print("  - 4 delegation tasks")
    
finally:
    session.close()
