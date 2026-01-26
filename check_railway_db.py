import os
import sys
sys.path.insert(0, os.getcwd())

from config import DATABASE_URL
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

print('🔍 Checking Railway database connection...')

try:
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Test connection - use different queries for different databases
    if 'sqlite' in str(engine.url):
        result = session.execute(text("SELECT sqlite_version()")).fetchone()
        print(f'✅ Connected to SQLite: {result[0]}')
    else:
        result = session.execute(text('SELECT version()')).fetchone()
        print(f'✅ Connected to PostgreSQL: {result[0][:50]}...')

    # Simple table check
    result = session.execute(text("SELECT COUNT(*) FROM users"))
    user_count = result.fetchone()[0]
    print(f'👤 Users table: {user_count} records')

    # Check subscription tiers
    if user_count > 0:
        result = session.execute(text("SELECT DISTINCT subscription_tier FROM users"))
        tiers = result.fetchall()
        print(f'🏷️  Subscription tiers in use: {[t[0] for t in tiers if t[0]]}')

    # Check for average_rating column (simple check)
    try:
        result = session.execute(text("SELECT average_rating FROM users LIMIT 1"))
        print('✅ average_rating column exists')
    except Exception:
        print('❌ average_rating column missing')

    session.close()
    print('🎯 Railway database check completed successfully')

except Exception as e:
    print(f'❌ Railway database error: {e}')