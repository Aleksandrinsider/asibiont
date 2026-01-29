"""
Скрипт для удаления дубликатов в полях interests, skills, goals в user_profiles
"""
from models import Session, UserProfile
from sqlalchemy import text

def remove_duplicates_from_field(value):
    """Удаляет дубликаты из строки, разделённой запятыми, сохраняя порядок"""
    if not value:
        return value
    
    items = [item.strip() for item in value.split(',') if item.strip()]
    seen = set()
    unique_items = []
    
    for item in items:
        item_lower = item.lower()
        if item_lower not in seen:
            seen.add(item_lower)
            unique_items.append(item)
    
    return ', '.join(unique_items)

def cleanup_profile_duplicates():
    session = Session()
    try:
        profiles = session.query(UserProfile).all()
        updated_count = 0
        
        for profile in profiles:
            changed = False
            
            # Очистка интересов
            if profile.interests:
                original = profile.interests
                cleaned = remove_duplicates_from_field(original)
                if original != cleaned:
                    print(f"User {profile.user_id} interests:")
                    print(f"  До:    {original}")
                    print(f"  После: {cleaned}")
                    profile.interests = cleaned
                    changed = True
            
            # Очистка навыков
            if profile.skills:
                original = profile.skills
                cleaned = remove_duplicates_from_field(original)
                if original != cleaned:
                    print(f"User {profile.user_id} skills:")
                    print(f"  До:    {original}")
                    print(f"  После: {cleaned}")
                    profile.skills = cleaned
                    changed = True
            
            # Очистка целей
            if profile.goals:
                original = profile.goals
                cleaned = remove_duplicates_from_field(original)
                if original != cleaned:
                    print(f"User {profile.user_id} goals:")
                    print(f"  До:    {original}")
                    print(f"  После: {cleaned}")
                    profile.goals = cleaned
                    changed = True
            
            if changed:
                updated_count += 1
        
        session.commit()
        print(f"\n✅ Обновлено профилей: {updated_count} из {len(profiles)}")
        
    except Exception as e:
        session.rollback()
        print(f"❌ Ошибка: {e}")
        raise
    finally:
        session.close()

if __name__ == "__main__":
    print("Начинаю очистку дубликатов в профилях...")
    cleanup_profile_duplicates()
