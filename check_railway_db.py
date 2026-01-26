import os
import sys
sys.path.insert(0, os.getcwd())

from config import DATABASE_URL
from sqlalchemy import create_engine, text, inspect
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

    # Use inspector for cross-database compatibility
    inspector = inspect(engine)

    # Check tables exist
    tables = inspector.get_table_names()
    print(f'📋 Tables found: {tables}')

    # Check users table structure
    if 'users' in tables:
        columns = inspector.get_columns('users')
        column_names = [col['name'] for col in columns]
        print('👤 Users table columns:')
        for col in column_names:
            print(f'  - {col}')

    # Check subscription tiers
    if 'users' in tables:
        result = session.execute(text("SELECT DISTINCT subscription_tier FROM users")).fetchall()
        print(f'🏷️  Subscription tiers in use: {[t[0] for t in result if t[0]]}')

    # Check for average_rating column
    if 'users' in tables:
        columns = inspector.get_columns('users')
        column_names = [col['name'] for col in columns]
        if 'average_rating' in column_names:
            print('✅ average_rating column exists')
        else:
            print('❌ average_rating column missing')

    session.close()
    print('🎯 Railway database check completed successfully')

except Exception as e:
    print(f'❌ Railway database error: {e}')