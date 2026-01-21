import os
if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

from models import SessionLocal, User, UserProfile, Subscription, Task, Post, Interaction
from datetime import datetime
import pytz

try:
    session = SessionLocal()
    
    print("="*70)
    print("КОМПЛЕКСНАЯ ПРОВЕРКА ЦЕЛОСТНОСТИ ДАННЫХ В БД")
    print("="*70)
    
    issues = []
    
    # 1. Проверка users.subscription_tier vs subscriptions.tier
    print("\n1️⃣ ПРОВЕРКА: users.subscription_tier vs subscriptions.tier")
    print("-"*70)
    
    active_subs = session.query(Subscription).filter_by(status='active').all()
    tier_mismatches = 0
    
    for sub in active_subs:
        user = session.query(User).filter_by(id=sub.user_id).first()
        if not user:
            continue
        
        # Проверяем истечение
        now = datetime.now(pytz.UTC)
        if sub.end_date and sub.end_date.tzinfo is None:
            sub.end_date = sub.end_date.replace(tzinfo=pytz.UTC)
        if sub.end_date and sub.end_date < now:
            continue
        
        user_tier = str(user.subscription_tier).split('.')[-1] if user.subscription_tier else None
        sub_tier = str(sub.tier).split('.')[-1] if sub.tier else None
        
        if user_tier != sub_tier:
            tier_mismatches += 1
            print(f"  ❌ @{user.username}: users.{user_tier} ≠ subscriptions.{sub_tier}")
            issues.append(f"Tier mismatch: @{user.username}")
    
    if tier_mismatches == 0:
        print("  ✅ Расхождений не найдено")
    else:
        print(f"  ⚠️ Найдено расхождений: {tier_mismatches}")
    
    # 2. Проверка User.average_rating vs UserProfile.average_rating
    print("\n2️⃣ ПРОВЕРКА: users.average_rating vs user_profiles.average_rating")
    print("-"*70)
    
    rating_mismatches = 0
    all_users = session.query(User).all()
    
    for user in all_users:
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            user_rating = user.average_rating if hasattr(user, 'average_rating') else None
            profile_rating = profile.average_rating if profile.average_rating is not None else None
            
            if user_rating != profile_rating:
                rating_mismatches += 1
                print(f"  ❌ @{user.username}: users.{user_rating} ≠ profile.{profile_rating}")
                issues.append(f"Rating mismatch: @{user.username}")
    
    if rating_mismatches == 0:
        print("  ✅ Расхождений не найдено")
    else:
        print(f"  ⚠️ Найдено расхождений: {rating_mismatches}")
    
    # 3. Проверка UserProfile существует для всех User
    print("\n3️⃣ ПРОВЕРКА: Наличие UserProfile для каждого User")
    print("-"*70)
    
    missing_profiles = 0
    for user in all_users:
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            missing_profiles += 1
            print(f"  ❌ @{user.username} (ID: {user.id}): профиль отсутствует")
            issues.append(f"Missing profile: @{user.username}")
    
    if missing_profiles == 0:
        print("  ✅ Все пользователи имеют профили")
    else:
        print(f"  ⚠️ Пользователей без профиля: {missing_profiles}")
    
    # 4. Проверка Subscription.telegram_id vs User.telegram_id
    print("\n4️⃣ ПРОВЕРКА: subscriptions.telegram_id vs users.telegram_id")
    print("-"*70)
    
    telegram_id_mismatches = 0
    all_subs = session.query(Subscription).all()
    
    for sub in all_subs:
        user = session.query(User).filter_by(id=sub.user_id).first()
        if user and sub.telegram_id and sub.telegram_id != user.telegram_id:
            telegram_id_mismatches += 1
            print(f"  ❌ Subscription {sub.id}: sub.telegram_id={sub.telegram_id} ≠ user.telegram_id={user.telegram_id}")
            issues.append(f"Telegram ID mismatch: subscription {sub.id}")
    
    if telegram_id_mismatches == 0:
        print("  ✅ Расхождений не найдено")
    else:
        print(f"  ⚠️ Найдено расхождений: {telegram_id_mismatches}")
    
    # 5. Проверка Subscription.username vs User.username
    print("\n5️⃣ ПРОВЕРКА: subscriptions.username vs users.username")
    print("-"*70)
    
    username_mismatches = 0
    for sub in all_subs:
        user = session.query(User).filter_by(id=sub.user_id).first()
        if user and sub.username and sub.username != user.username:
            username_mismatches += 1
            print(f"  ❌ Subscription {sub.id}: sub.username={sub.username} ≠ user.username={user.username}")
            issues.append(f"Username mismatch: subscription {sub.id}")
    
    if username_mismatches == 0:
        print("  ✅ Расхождений не найдено")
    else:
        print(f"  ⚠️ Найдено расхождений: {username_mismatches}")
    
    # 6. Проверка orphaned UserProfiles (профили без пользователей)
    print("\n6️⃣ ПРОВЕРКА: Orphaned UserProfiles (профили без пользователей)")
    print("-"*70)
    
    all_profiles = session.query(UserProfile).all()
    orphaned_profiles = 0
    
    for profile in all_profiles:
        user = session.query(User).filter_by(id=profile.user_id).first()
        if not user:
            orphaned_profiles += 1
            print(f"  ❌ UserProfile {profile.id}: user_id={profile.user_id} не существует")
            issues.append(f"Orphaned profile: {profile.id}")
    
    if orphaned_profiles == 0:
        print("  ✅ Все профили привязаны к существующим пользователям")
    else:
        print(f"  ⚠️ Orphaned профилей: {orphaned_profiles}")
    
    # 7. Проверка orphaned Subscriptions
    print("\n7️⃣ ПРОВЕРКА: Orphaned Subscriptions (подписки без пользователей)")
    print("-"*70)
    
    orphaned_subs = 0
    for sub in all_subs:
        user = session.query(User).filter_by(id=sub.user_id).first()
        if not user:
            orphaned_subs += 1
            print(f"  ❌ Subscription {sub.id}: user_id={sub.user_id} не существует")
            issues.append(f"Orphaned subscription: {sub.id}")
    
    if orphaned_subs == 0:
        print("  ✅ Все подписки привязаны к существующим пользователям")
    else:
        print(f"  ⚠️ Orphaned подписок: {orphaned_subs}")
    
    # 8. Проверка Tasks с несуществующими user_id
    print("\n8️⃣ ПРОВЕРКА: Tasks с несуществующими user_id")
    print("-"*70)
    
    all_tasks = session.query(Task).all()
    orphaned_tasks = 0
    
    for task in all_tasks:
        user = session.query(User).filter_by(id=task.user_id).first()
        if not user:
            orphaned_tasks += 1
            if orphaned_tasks <= 5:  # Показываем только первые 5
                print(f"  ❌ Task {task.id}: user_id={task.user_id} не существует")
            issues.append(f"Orphaned task: {task.id}")
    
    if orphaned_tasks == 0:
        print("  ✅ Все задачи привязаны к существующим пользователям")
    else:
        print(f"  ⚠️ Orphaned задач: {orphaned_tasks}")
        if orphaned_tasks > 5:
            print(f"  (показаны первые 5)")
    
    # 9. Проверка Posts с несуществующими user_id
    print("\n9️⃣ ПРОВЕРКА: Posts с несуществующими user_id")
    print("-"*70)
    
    all_posts = session.query(Post).all()
    orphaned_posts = 0
    
    for post in all_posts:
        user = session.query(User).filter_by(id=post.user_id).first()
        if not user:
            orphaned_posts += 1
            print(f"  ❌ Post {post.id}: user_id={post.user_id} не существует")
            issues.append(f"Orphaned post: {post.id}")
    
    if orphaned_posts == 0:
        print("  ✅ Все посты привязаны к существующим пользователям")
    else:
        print(f"  ⚠️ Orphaned постов: {orphaned_posts}")
    
    # 10. Проверка дубликатов Subscription для одного пользователя
    print("\n🔟 ПРОВЕРКА: Дубликаты активных подписок для одного пользователя")
    print("-"*70)
    
    from sqlalchemy import func
    duplicate_subs = session.query(
        Subscription.user_id, 
        func.count(Subscription.id).label('count')
    ).filter_by(status='active').group_by(Subscription.user_id).having(func.count(Subscription.id) > 1).all()
    
    if len(duplicate_subs) == 0:
        print("  ✅ Дубликатов не найдено")
    else:
        print(f"  ⚠️ Пользователей с дубликатами подписок: {len(duplicate_subs)}")
        for user_id, count in duplicate_subs:
            user = session.query(User).filter_by(id=user_id).first()
            print(f"  ❌ @{user.username if user else user_id}: {count} активных подписок")
            issues.append(f"Duplicate subscriptions: user_id={user_id}")
    
    # ИТОГОВЫЙ ОТЧЁТ
    print("\n" + "="*70)
    print("ИТОГОВЫЙ ОТЧЁТ")
    print("="*70)
    
    if len(issues) == 0:
        print("\n✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ! База данных в порядке.")
    else:
        print(f"\n⚠️ НАЙДЕНО ПРОБЛЕМ: {len(issues)}")
        print("\nКритичные проблемы для исправления:")
        
        # Группируем проблемы
        critical = [i for i in issues if 'mismatch' in i.lower()]
        orphaned = [i for i in issues if 'orphaned' in i.lower()]
        missing = [i for i in issues if 'missing' in i.lower()]
        duplicates = [i for i in issues if 'duplicate' in i.lower()]
        
        if critical:
            print(f"  - Расхождения данных: {len(critical)}")
        if missing:
            print(f"  - Отсутствующие профили: {len(missing)}")
        if orphaned:
            print(f"  - Orphaned записи: {len(orphaned)}")
        if duplicates:
            print(f"  - Дубликаты: {len(duplicates)}")
    
    print("\n" + "="*70)
    
    session.close()
    
except Exception as e:
    print(f"\n❌ Ошибка: {e}")
    import traceback
    traceback.print_exc()
