import os
if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

from models import SessionLocal, User, Post, UserProfile

session = SessionLocal()

print("=== Проверка постов в продакшн БД ===\n")

# Проверяем посты @snowboarder_max
snowboarder = session.query(User).filter(User.username == 'snowboarder_max').first()
if snowboarder:
    posts = session.query(Post).filter(Post.user_id == snowboarder.id).order_by(Post.created_at.desc()).all()
    print(f"✅ @snowboarder_max (ID: {snowboarder.id})")
    print(f"   Постов: {len(posts)}\n")
    
    if posts:
        print("Последние посты:")
        for i, post in enumerate(posts[:5], 1):
            preview = post.content[:60] + '...' if len(post.content) > 60 else post.content
            print(f"  {i}. {preview}")
            print(f"     Создан: {post.created_at}")
    else:
        print("⚠️  Постов нет")
else:
    print("❌ @snowboarder_max не найден")

# Проверяем избранное у @aleksandrinsider
print("\n" + "="*50)
aleksandr = session.query(User).filter(User.username == 'aleksandrinsider').first()
if aleksandr:
    profile = session.query(UserProfile).filter(UserProfile.user_id == aleksandr.telegram_id).first()
    print(f"\n✅ @aleksandrinsider (ID: {aleksandr.telegram_id})")
    if profile:
        favorites = profile.favorite_contacts.split(',') if profile.favorite_contacts else []
        print(f"   Избранных контактов: {len(favorites)}")
        if favorites:
            print(f"   Список: {', '.join(['@' + f for f in favorites if f])}")
            if 'snowboarder_max' in favorites:
                print("\n   ✅ @snowboarder_max в избранном!")
            else:
                print("\n   ⚠️  @snowboarder_max НЕ в избранном")
        else:
            print("   ⚠️  Нет избранных контактов")
    else:
        print("   ⚠️  Профиль не найден")
else:
    print("❌ @aleksandrinsider не найден")

session.close()

print("\n" + "="*50)
print("\n📋 Инструкция:")
print("1. Откройте https://asibiont.ru/dashboard?telegram_id=146333757")
print("2. Нажмите на ссылку 'Лента новостей'")
print("3. Вы увидите посты от @snowboarder_max")
