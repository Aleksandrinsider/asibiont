import os
os.environ['LOCAL'] = '0'

from models import Session, User
from ai_integration.handlers import get_partners_list

s = Session()
u = s.query(User).filter_by(username='aleksandrinsider').first()
print(f'User ID: {u.id}, Telegram ID: {u.telegram_id}')

partners = get_partners_list(user_id=u.id, session=s)
print(f'\nTotal partners: {len(partners)}')

if partners:
    print(f'First partner type: {type(partners[0]).__name__}')
    print(f'Partner has username: {hasattr(partners[0], "username")}')
    
    # Count by type if attribute exists
    if hasattr(partners[0], 'partner_type'):
        rec = [p for p in partners if p.partner_type == 'recommended']
        print(f'Recommended: {len(rec)}')

s.close()
