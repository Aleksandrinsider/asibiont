import os
os.environ['LOCAL'] = '0'

from models import Session, User, UserProfile
from ai_integration.handlers import get_partners_list

s = Session()
u = s.query(User).filter_by(username='aleksandrinsider').first()
print(f'User: @{u.username}')

# Get user profile
profile = s.query(UserProfile).filter_by(user_id=u.id).first()
if profile:
    print(f'Interests: {profile.interests}')
    print(f'Skills: {profile.skills}')
    print(f'City: {profile.city}')

partners = get_partners_list(user_id=u.id, session=s)
print(f'\n=== Total partners: {len(partners)} ===\n')

for i, p in enumerate(partners[:10], 1):
    partner_user = s.query(User).filter_by(id=p.user_id).first()
    print(f'{i}. @{partner_user.username if partner_user else "unknown"}')
    print(f'   Interests: {p.interests}')
    print(f'   Skills: {p.skills}')
    print(f'   City: {p.city}')
    
    # Check for common attributes
    if hasattr(p, 'common_interests') and p.common_interests:
        print(f'   ✓ Common interests: {p.common_interests}')
    if hasattr(p, 'common_skills') and p.common_skills:
        print(f'   ✓ Common skills: {p.common_skills}')
    print()

s.close()
