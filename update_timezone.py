"""Update timezone for user to Europe/Moscow"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import User

DATABASE_URL = "postgresql://postgres:EzKuRTaADIaiEaFWQHvluInZpiMlUcHt@shortline.proxy.rlwy.net:42709/railway"

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

# Update timezone for user with telegram_id=146333757
user = session.query(User).filter_by(telegram_id=146333757).first()
if user:
    old_tz = user.timezone
    user.timezone = 'Europe/Moscow'
    session.commit()
    print(f"✅ Updated timezone for telegram_id=146333757: {old_tz} -> Europe/Moscow")
else:
    print("❌ User not found")

session.close()
