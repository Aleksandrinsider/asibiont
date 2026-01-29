"""
Тест новой логики добавления данных в профиль вместо замены
"""
import asyncio
from models import User, UserProfile, Session
from ai_integration.handlers import update_profile, update_user_memory_async

async def test_append_logic():
    """Проверяем что данные добавляются, а не заменяются"""
    session = Session()
    
    # Находим любого пользователя для теста (или создаем тестового)
    user = session.query(User).first()
    if not user:
        print("⚙️ Создаем тестового пользователя")
        user = User(
            telegram_id=999999999,
            username="test_append_user",
            first_name="Test"
        )
        session.add(user)
        session.commit()
    
    print(f"✅ Используем пользователя: {user.username} (ID: {user.telegram_id})")
    
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if not profile:
        print("⚙️ Создаем профиль")
        profile = UserProfile(
            user_id=user.id,
            interests="программирование",
            skills="Python",
            goals="изучить AI"
        )
        session.add(profile)
        session.commit()
    
    print("📋 НАЧАЛЬНОЕ СОСТОЯНИЕ ПРОФИЛЯ:")
    print(f"   Интересы: {profile.interests}")
    print(f"   Навыки: {profile.skills}")
    print(f"   Цели: {profile.goals}")
    print()
    
    # Тест 1: Добавление нового интереса через update_profile
    print("🧪 ТЕСТ 1: Добавляем 'покер' через update_profile")
    result1 = update_profile(
        user_id=user.telegram_id,
        interests="покер",
        session=session,
        close_session=False
    )
    print(f"   Результат: {result1}")
    session.refresh(profile)
    print(f"   Интересы после: {profile.interests}")
    print()
    
    # Тест 2: Попытка добавить тот же интерес (должен определить дубликат)
    print("🧪 ТЕСТ 2: Пытаемся добавить 'покер' еще раз")
    result2 = update_profile(
        user_id=user.telegram_id,
        interests="покер",
        session=session,
        close_session=False
    )
    print(f"   Результат: {result2}")
    session.refresh(profile)
    print(f"   Интересы после: {profile.interests}")
    print()
    
    # Тест 3: Добавление интереса через update_user_memory_async
    print("🧪 ТЕСТ 3: Добавляем 'шахматы' через update_user_memory_async")
    result3 = await update_user_memory_async(
        memory_type="interest",
        content="шахматы",
        user_id=user.telegram_id,
        session=session,
        close_session=False
    )
    print(f"   Результат: {result3}")
    session.refresh(profile)
    print(f"   Интересы после: {profile.interests}")
    print()
    
    # Тест 4: Добавление навыка
    print("🧪 ТЕСТ 4: Добавляем навык 'блеф'")
    result4 = update_profile(
        user_id=user.telegram_id,
        skills="блеф",
        session=session,
        close_session=False
    )
    print(f"   Результат: {result4}")
    session.refresh(profile)
    print(f"   Навыки после: {profile.skills}")
    print()
    
    # Тест 5: Добавление цели через update_user_memory_async
    print("🧪 ТЕСТ 5: Добавляем цель 'стать профессиональным игроком'")
    result5 = await update_user_memory_async(
        memory_type="goal",
        content="стать профессиональным игроком",
        user_id=user.telegram_id,
        session=session,
        close_session=False
    )
    print(f"   Результат: {result5}")
    session.refresh(profile)
    print(f"   Цели после: {profile.goals}")
    print()
    
    print("📋 ФИНАЛЬНОЕ СОСТОЯНИЕ ПРОФИЛЯ:")
    print(f"   Интересы: {profile.interests}")
    print(f"   Навыки: {profile.skills}")
    print(f"   Цели: {profile.goals}")
    print()
    
    # Откатываем изменения (это тест)
    session.rollback()
    print("✅ Изменения откачены (это был тест)")
    
    session.close()

if __name__ == "__main__":
    asyncio.run(test_append_logic())
