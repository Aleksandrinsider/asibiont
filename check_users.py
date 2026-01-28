#!/usr/bin/env python3
"""
Script to check users in Railway database.
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import User
from config import DATABASE_URL

def check_users():
    """Check users in database"""

    # Connect to database
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Get all users
        users = session.query(User).all()
        print(f"Found {len(users)} users:")

        for user in users:
            print(f"ID: {user.id}, Telegram ID: {user.telegram_id}, Username: {user.username}, First Name: {user.first_name}")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    check_users()