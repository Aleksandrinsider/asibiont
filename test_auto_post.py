"""
Тест генерации автопоста для конкретного пользователя
"""
import asyncio
import sys
from models import Session, User
from auto_post_service import generate_progress_post

async def test_generate_post(username):
    """Generate test post for specific user"""
    session = Session()
    
    try:
        # Find user by username
        user = session.query(User).filter(User.username.like(f"%{username}%")).first()
        
        if not user:
            print(f"❌ Пользователь с username '{username}' не найден")
            return
        
        print(f"✅ Найден пользователь: @{user.username} (ID: {user.telegram_id})")
        print(f"   Timezone: {user.timezone}")
        print()
        
        print("🤖 Генерирую пост...")
        print("=" * 60)
        
        # Generate post
        content = await generate_progress_post(user.telegram_id, session)
        
        print()
        print("📝 СГЕНЕРИРОВАННЫЙ ПОСТ:")
        print("=" * 60)
        if content:
            print(content)
            print("=" * 60)
            print()
            
            # Check for technical phrases
            bad_phrases = ["На какое время", "поставить задачу", "NEED_TIME", "ОБЯЗАТЕЛЬНО"]
            found_issues = [phrase for phrase in bad_phrases if phrase in content]
            
            if found_issues:
                print(f"⚠️ ВНИМАНИЕ! Найдены технические фразы: {found_issues}")
            else:
                print("✅ Технических фраз не обнаружено!")
        else:
            print("❌ Не удалось сгенерировать пост")
            print("Возможные причины:")
            print("- У пользователя нет профиля")
            print("- Нет статистики задач")
            print("- Ошибка API DeepSeek")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()


if __name__ == "__main__":
    username = sys.argv[1] if len(sys.argv) > 1 else "test_games_4"
    asyncio.run(test_generate_post(username))
