"""Script to clear promo codes, subscriptions from database and Redis"""
import os
from dotenv import load_dotenv

load_dotenv()

# Do not set LOCAL=1 to use production database
from models import Session, PromoCode, Subscription
from sqlalchemy import text

def clear_promo_and_subs():
    """Clear promo codes and subscriptions from database"""
    session = Session()
    try:
        print("Clearing promo codes and subscriptions...")

        # Delete promo codes
        promo_count = session.query(PromoCode).delete()
        print(f"✓ PromoCodes cleared ({promo_count} records)")

        # Delete subscriptions
        sub_count = session.query(Subscription).delete()
        print(f"✓ Subscriptions cleared ({sub_count} records)")

        session.commit()
        print("\n✅ Promo codes and subscriptions cleared!")

    except Exception as e:
        session.rollback()
        print(f"\n❌ Error clearing data: {e}")
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
    print("CLEARING PROMO CODES, SUBSCRIPTIONS AND REDIS")
    print("=" * 50)

# No confirmation for production
    clear_promo_and_subs()
    clear_redis()

    print("\n" + "=" * 50)
    print("✅ PROMO CODES, SUBSCRIPTIONS AND REDIS CLEARED")
    print("=" * 50)