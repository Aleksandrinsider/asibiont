"""
Демонстрация AI Marketing Agent
Показывает как агент решает проблему привлечения клиентов
"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.autonomous_agent import chat_with_ai
from models import Session, User, UserProfile, Task, SubscriptionTier

async def demo_marketing_agent():
    """Демо: Пользователь не может привлечь клиентов для AI-бота"""
    
    test_user_id = 111222333
    session = Session()
    
    print("\n" + "="*80)
    print("🚀 ДЕМО: AI Marketing Agent в действии")
    print("="*80)
    print("\nСценарий: Создатель AI-бота не может привлечь первых пользователей")
    print("-"*80 + "\n")
    
    try:
        # Очистка
        session.query(Task).filter_by(user_id=session.query(User).filter_by(telegram_id=test_user_id).first().id if session.query(User).filter_by(telegram_id=test_user_id).first() else None).delete()
        session.query(UserProfile).filter_by(user_id=session.query(User).filter_by(telegram_id=test_user_id).first().id if session.query(User).filter_by(telegram_id=test_user_id).first() else None).delete()
        session.query(User).filter_by(telegram_id=test_user_id).delete()
        session.commit()
        
        # Создание пользователя
        user = User(
            telegram_id=test_user_id,
            username="startup_founder",
            first_name="Алекс",
            subscription_tier=SubscriptionTier.PREMIUM
        )
        session.add(user)
        session.commit()
        
        # Профиль
        profile = UserProfile(
            user_id=user.id,
            city="Москва",
            company="TechBot AI",
            position="Основатель",
            skills="Python, AI, предпринимательство",
            interests="стартапы, технологии, маркетинг"
        )
        session.add(profile)
        session.commit()
        
        print("\n" + "="*80)
        print("ТЕСТ 1: Генерация маркетингового контента")
        print("="*80)
        print("\n[Пользователь]: 'Помоги написать пост про мой AI-бот для Telegram. Целевая аудитория - предприниматели 25-40 лет.'")
        print()
        
        result1 = await chat_with_ai(
            "Помоги написать пост про мой AI-бот для Telegram. Целевая аудитория - предприниматели 25-40 лет.",
            user_id=test_user_id,
            db_session=session
        )
        
        print(f"[AI]: {result1['response'][:500]}...")
        print()
        
        await asyncio.sleep(2)
        
        print("\n" + "="*80)
        print("ТЕСТ 2: Создание контент-календаря")
        print("="*80)
        print("\n[Пользователь]: 'Мне нужен контент-план на неделю для привлечения клиентов. Ниша - AI-боты для бизнеса.'")
        print()
        
        result2 = await chat_with_ai(
            "Мне нужен контент-план на неделю для привлечения клиентов. Ниша - AI-боты для бизнеса.",
            user_id=test_user_id,
            db_session=session
        )
        
        print(f"[AI]: {result2['response'][:500]}...")
        print()
        
        await asyncio.sleep(2)
        
        print("\n" + "="*80)
        print("ТЕСТ 3: Growth Hacks (главная проблема)")
        print("="*80)
        print("\n[Пользователь]: 'У меня проблема: я создал AI-бота но никак не могу привлечь пользователей. Сейчас 0 пользователей, хочу привлечь первых 100.'")
        print()
        
        result3 = await chat_with_ai(
            "У меня проблема: я создал AI-бота но никак не могу привлечь пользователей. Сейчас 0 пользователей, хочу привлечь первых 100.",
            user_id=test_user_id,
            db_session=session
        )
        
        print(f"[AI]: {result3['response'][:600]}...")
        print()
        
        # Проверка созданных задач
        print("\n" + "="*80)
        print("📊 РЕЗУЛЬТАТЫ")
        print("="*80 + "\n")
        
        tasks = session.query(Task).filter_by(user_id=user.id).all()
        
        if tasks:
            print(f"✅ AI автоматически создал {len(tasks)} задач:\n")
            for i, task in enumerate(tasks, 1):
                print(f"{i}. {task.title}")
                if task.description:
                    print(f"   └─ {task.description[:80]}...")
                print()
        else:
            print("⚠️  Задачи не созданы")
        
        print("="*80)
        print("\n💡 ВЫВОДЫ:")
        print("1. AI автоматически определяет проблему (нет клиентов)")
        print("2. Предлагает конкретные решения через маркетинговые инструменты")
        print("3. Генерирует готовый контент и actionable шаги")
        print("4. Создает задачи для реализации стратегий")
        print()
        print("🎯 Агент превратился в личного маркетолога!")
        print("="*80 + "\n")
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()


async def quick_test():
    """Быстрый тест одной функции"""
    from ai_integration.marketing_agent import suggest_growth_hacks
    
    print("\n" + "="*80)
    print("🧪 QUICK TEST: suggest_growth_hacks")
    print("="*80 + "\n")
    
    result = await suggest_growth_hacks(
        niche="AI-боты для бизнеса",
        current_users=0,
        goal_users=100
    )
    
    print(result['message'])
    print("\n" + "="*80 + "\n")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument('--quick', action='store_true', help='Quick test without full dialog')
    args = parser.parse_args()
    
    if args.quick:
        asyncio.run(quick_test())
    else:
        asyncio.run(demo_marketing_agent())
