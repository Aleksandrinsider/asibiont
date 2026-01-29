"""Debug: почему sport_maria не показывается"""
import os
os.environ['LOCAL'] = '1'

from models import Session, User, UserProfile

session = Session()

test_user = session.query(User).filter_by(username='test_user').first()
maria = session.query(User).filter_by(username='sport_maria').first()

test_prof = session.query(UserProfile).filter_by(user_id=test_user.id).first()
maria_prof = session.query(UserProfile).filter_by(user_id=maria.id).first()

print("test_user интересы:", test_prof.interests if test_prof else "нет")
print("maria интересы:", maria_prof.interests if maria_prof else "нет")

# Проверка совпадений
if test_prof and maria_prof and test_prof.interests and maria_prof.interests:
    test_interests = set(i.strip().lower() for i in test_prof.interests.split(','))
    maria_interests = set(i.strip().lower() for i in maria_prof.interests.split(','))
    
    print("\ntest_interests:", test_interests)
    print("maria_interests:", maria_interests)
    print("Пересечение:", test_interests & maria_interests)
    
    if not (test_interests & maria_interests):
        print("\n❌ НЕТ ПРЯМЫХ СОВПАДЕНИЙ - поэтому maria не показывается!")
        print("Нужно чтобы был хотя бы один общий интерес для базового показа")

session.close()
