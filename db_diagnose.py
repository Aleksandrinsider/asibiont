#!/usr/bin/env python
"""Диагностика — отдельные запросы для избежания transaction abort."""
import sys
sys.path.insert(0, '.')
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_URL = 'postgresql://postgres:upZTbJrZvoxnoSPdUDaOwnLuOvnNSbML@nozomi.proxy.rlwy.net:52451/railway'
engine = create_engine(DATABASE_URL)
user_id = 1

def query(label, sql, params=None):
    try:
        Session = sessionmaker(bind=engine)
        s = Session()
        result = s.execute(text(sql), params or {})
        rows = result.fetchall()
        s.close()
        print(f'\n=== {label} ===')
        for r in rows:
            print(f'  {r}')
        return rows
    except Exception as e:
        s.close()
        print(f'\n=== {label} ERROR: {e} ===')
        return []

# 1. Actual user.token_balance
query('USER TOKEN BALANCE', 
    'SELECT id, telegram_id, token_balance, tokens_spent FROM users WHERE id = :uid',
    {'uid': user_id})

# 2. Token transactions by action
query('TOKEN TRANSACTIONS BY ACTION',
    'SELECT action, COUNT(*) as cnt, SUM(amount) as total, MIN(amount), MAX(amount) '
    'FROM token_transactions WHERE user_id = :uid '
    'GROUP BY action ORDER BY total ASC',
    {'uid': user_id})

# 3. Top 20 largest debits
query('TOP 20 LARGEST DEBITS',
    'SELECT id, action, amount, substring(COALESCE(description,\'\')::text,1,80) as desc_short, created_at '
    'FROM token_transactions WHERE user_id = :uid '
    'ORDER BY amount ASC LIMIT 20',
    {'uid': user_id})

# 4. Last 20 credits
query('LAST 20 CREDITS',
    'SELECT id, action, amount, substring(COALESCE(description,\'\')::text,1,80) as desc_short, created_at '
    'FROM token_transactions WHERE user_id = :uid AND amount > 0 '
    'ORDER BY created_at DESC LIMIT 20',
    {'uid': user_id})

# 5. Contacted contacts by source
query('CONTACTED CONTACTS BY SOURCE',
    'SELECT COALESCE(source,\'unknown\') as src, COUNT(*) as cnt '
    'FROM email_contacts WHERE user_id = :uid AND status = \'contacted\' '
    'GROUP BY source ORDER BY cnt DESC',
    {'uid': user_id})

# 6. All outreach records from today (all statuses)
query('OUTREACH CREATED TODAY',
    'SELECT status, COUNT(*) as cnt '
    'FROM email_outreach WHERE user_id = :uid AND created_at::date = CURRENT_DATE '
    'GROUP BY status ORDER BY cnt DESC',
    {'uid': user_id})

# 7. Daily token usage last 14 days
query('DAILY TOKEN USAGE',
    'SELECT DATE(created_at) as day, SUM(amount) as total, COUNT(*) as cnt '
    'FROM token_transactions WHERE user_id = :uid '
    'GROUP BY DATE(created_at) ORDER BY day DESC LIMIT 14',
    {'uid': user_id})

# 8. Check autopilot_enabled column (singular, the one that may exist)
query('AUTOPILOT_ENABLED',
    'SELECT autopilot_enabled FROM users WHERE id = :uid',
    {'uid': user_id})

# 9. All new contacts (to see how many were added but never sent)
query('NEW CONTACTS SAMPLE',
    'SELECT id, email, name, company, source, created_at '
    'FROM email_contacts WHERE user_id = :uid AND status = \'new\' '
    'ORDER BY created_at DESC LIMIT 10',
    {'uid': user_id})

# 10. Count total contacts
query('TOTAL CONTACTS COUNT',
    'SELECT COUNT(*) FROM email_contacts WHERE user_id = :uid',
    {'uid': user_id})

# 11. autopilot_enabled from users
query('AUTOPILOT FLAGS',
    'SELECT autopilot_enabled FROM users WHERE id = :uid',
    {'uid': user_id})

# 12. Check agent_activity_log separately
try:
    Session = sessionmaker(bind=engine)
    s = Session()
    has_aal = s.execute(text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'agent_activity_log')")).scalar()
    if has_aal:
        aal = s.execute(text("""
            SELECT action, status, created_at 
            FROM agent_activity_log 
            WHERE user_id = :uid AND action LIKE '%email%'
            ORDER BY created_at DESC LIMIT 20
        """), {'uid': user_id}).fetchall()
        print(f'\n=== AGENT ACTIVITY LOG (email actions) ===')
        for a in aal:
            print(f'  action={a[0]} status={a[1]} created={a[2]}')
    else:
        print('\n=== NO agent_activity_log TABLE ===')
    s.close()
except Exception as e:
    s.close()
    print(f'\n=== AGENT ACTIVITY LOG ERROR: {e} ===')

# 13. Check FREE_ACCESS_MODE from config.py  
try:
    with open('config.py', 'r', encoding='utf-8') as f:
        for line in f:
            if 'FREE_ACCESS_MODE' in line and not line.strip().startswith('#'):
                print(f'\n=== CONFIG: {line.strip()} ===')
except Exception as e:
    print(f'\n=== CONFIG READ ERROR: {e} ===')

# 14. Read ACTION_COSTS from token_service.py
try:
    with open('token_service.py', 'r', encoding='utf-8') as f:
        content = f.read()
        import re
        action_costs = re.search(r'ACTION_COSTS\s*=\s*\{([^}]+)\}', content, re.DOTALL)
        if action_costs:
            print(f'\n=== ACTION_COSTS ===')
            print(action_costs.group(0)[:500])
        default_cost = re.search(r'DEFAULT_TOOL_COST\s*=\s*(\d+)', content)
        if default_cost:
            print(f'\n=== DEFAULT_TOOL_COST = {default_cost.group(1)} ===')
except Exception as e:
    print(f'\n=== TOKEN_SERVICE READ ERROR: {e} ===')
