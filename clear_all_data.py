"""Script to clear all data from database and Redis"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

# Set LOCAL mode for SQLite
os.environ['LOCAL'] = '1'

from models import Session, User, Task, UserProfile, Interaction, UserRating, PromoCode, PaymentHistory, Subscription
from sqlalchemy import text

def clear_database():
    """Clear all data from database"""
    session = Session()
    try:
        print("Clearing database...")
        
        # Delete all records with error handling for each table
        tables = [
            (Interaction, "Interactions"),
            (UserRating, "UserRatings"),
            (Task, "Tasks"),
            (PaymentHistory, "PaymentHistory"),
            (Subscription, "Subscriptions"),
            (PromoCode, "PromoCodes"),
            (UserProfile, "UserProfiles"),
            (User, "Users")
        ]
        
        for model, name in tables:
            try:
                count = session.query(model).delete()
                print(f"✓ {name} cleared ({count} records)")
            except Exception as e:
                print(f"⚠️  {name} skipped: {e}")
        
        session.commit()
        print("\n✅ Database completely cleared!")
        
    except Exception as e:
        session.rollback()
        print(f"\n❌ Error clearing database: {e}")
        raise
    finally:
        session.close()

def clear_redis():
    """Clear all data from Redis"""
    try:
        from redis import Redis
        
        # Connect to Redis
        redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379')
        redis_client = Redis.from_url(redis_url, decode_responses=True)
        
        print("\nClearing Redis...")
        redis_client.flushdb()
        print("✅ Redis completely cleared!")
        
    except Exception as e:
        print(f"\n⚠️ Redis clearing skipped (not critical): {e}")

if __name__ == "__main__":
    print("=" * 50)
    print("CLEARING ALL DATA")
    print("=" * 50)
    
    response = input("\n⚠️  This will DELETE ALL data. Continue? (yes/no): ")
    if response.lower() != 'yes':
        print("Aborted.")
        sys.exit(0)
    
    clear_database()
    clear_redis()
    
    print("\n" + "=" * 50)
    print("✅ ALL DATA CLEARED SUCCESSFULLY")
    print("=" * 50)
