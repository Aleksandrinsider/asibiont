import os
os.environ['LOCAL'] = '1'

from models import Session, User, UserProfile

s = Session()
u = s.query(User).first()
if u:
    p = s.query(UserProfile).filter_by(user_id=u.id).first()
    if p:
        # Добавляем спорт к интересам
        current_interests = p.interests or ""
        if "спорт" not in current_interests.lower():
            p.interests = current_interests + ", спорт" if current_interests else "спорт"
            s.commit()
            print(f"Added 'спорт' to interests: {p.interests}")
        else:
            print(f"Already has 'спорт': {p.interests}")
s.close()
