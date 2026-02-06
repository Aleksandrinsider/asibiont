"""
Тест реального поведения Premium автоматизации
Проверяет что происходит когда Premium создаёт задачу
"""

import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from models import Session, User, UserProfile, Task, SubscriptionTier, Base, engine
from ai_integration.premium_simple import trigger_premium_automation_realtime, get_premium_recommendations_for_prompt
from datetime import datetime, timedelta
import pytz
import json


async def test_premium_flow():
    """Тестирует полный flow Premium автоматизации"""
    
    print("\n" + "="*70)
    print("ТЕСТ PREMIUM АВТОМАТИЗАЦИИ")
    print("="*70)
    
    # Создаём тестовую БД
    Base.metadata.create_all(engine)
    session = Session()
    
    try:
        # Создаём Premium пользователя
        premium_user = User(
            telegram_id=111111,
            username='premium_test',
            first_name='Premium',
            subscription_tier=SubscriptionTier.PREMIUM,
            timezone='Europe/Moscow'
        )
        session.add(premium_user)
        session.commit()
        
        premium_profile = UserProfile(
            user_id=premium_user.id,
            interests='Python, AI, автоматизация',
            goals='Создать AI-платформу для автоматизации бизнес-процессов',
            city='Moscow'
        )
        session.add(premium_profile)
        session.commit()
        
        # Создаём потенциального партнёра
        partner = User(
            telegram_id=222222,
            username='partner_test',
            first_name='Partner',
            subscription_tier=SubscriptionTier.LIGHT,
            timezone='Europe/Moscow'
        )
        session.add(partner)
        session.commit()
        
        partner_profile = UserProfile(
            user_id=partner.id,
            interests='AI, машинное обучение, Python',
            skills='Python, FastAPI, ML',
            city='Moscow'
        )
        session.add(partner_profile)
        session.commit()
        
        print("\n✅ Создали 2 тестовых пользователя:")
        print(f"   Premium: {premium_user.username} (интересы: {premium_profile.interests})")
        print(f"   Partner: {partner.username} (интересы: {partner_profile.interests})")
        
        # Premium создаёт задачу
        task = Task(
            user_id=premium_user.id,
            title='Найти Python разработчика',
            description='Найти Python разработчика для AI проекта',
            status='pending'
        )
        session.add(task)
        session.commit()
        
        print(f"\n✅ Premium создал задачу: '{task.title}'")
        
        # Триггерим Premium автоматизацию
        print("\n🚀 Запускаем Premium автоматизацию...")
        try:
            result = await trigger_premium_automation_realtime(
                premium_user_id=premium_user.telegram_id,
                task_id=task.id,
                task_description=task.description
            )
        except Exception as e:
            print(f"\n❌ Ошибка при запуске автоматизации: {e}")
            result = {"error": str(e)}
        
        print(f"\n📊 Результат автоматизации:")
        print(f"   Проанализировано целей: {result.get('items_analyzed', 0)}")
        print(f"   Найдено релевантных людей: {result.get('relevant_users_found', 0)}")
        print(f"   Сохранено рекомендаций: {result.get('recommendations_saved', 0)}")
        
        if result.get('saved_details'):
            print(f"\n   Детали:")
            for detail in result['saved_details']:
                print(f"   - {detail['user']}: {detail['match_reason']}")
        
        # Проверяем что сохранилось в профиле партнёра
        session.refresh(partner_profile)
        
        print("\n🔍 Проверяем профиль партнёра:")
        if partner_profile.pending_premium_recommendations:
            recs = json.loads(partner_profile.pending_premium_recommendations)
            print(f"   Найдено {len(recs)} рекомендаций")
            for i, rec in enumerate(recs, 1):
                print(f"\n   Рекомендация #{i}:")
                print(f"   - Возможность: {rec.get('opportunity', 'N/A')}")
                print(f"   - Причина: {rec.get('match_reason', 'N/A')}")
                print(f"   - Показано раз: {rec.get('shown_count', 0)}")
        else:
            print("   ❌ Рекомендаций не найдено!")
        
        # Проверяем что увидит партнёр в промпте
        print("\n📝 Что увидит AI партнёра в следующем диалоге:")
        prompt_section = get_premium_recommendations_for_prompt(
            user_id=partner.telegram_id,
            session=session
        )
        
        if prompt_section:
            print(prompt_section)
        else:
            print("   ❌ Пустой промпт!")
        
        # Проверяем что происходит с Premium пользователем
        print("\n\n" + "="*70)
        print("ПРОВЕРКА: ЧТО ПОЛУЧАЕТ PREMIUM ПОЛЬЗОВАТЕЛЬ?")
        print("="*70)
        
        premium_prompt = get_premium_recommendations_for_prompt(
            user_id=premium_user.telegram_id,
            session=session
        )
        
        if premium_prompt:
            print("\n📝 Premium видит в промпте:")
            print(premium_prompt)
        else:
            print("\n❌ Premium НЕ видит никаких инсайтов о найденных партнёрах!")
        
        # ИТОГОВЫЙ ВЫВОД
        print("\n\n" + "="*70)
        print("ИТОГИ ТЕСТА")
        print("="*70)
        
        print("\n❌ ПРОБЛЕМА: Реализация НЕ соответствует описанию!")
        print("\nОПИСАНИЕ:")
        print('  "AI на автопилоте: сам находит партнёров, инициирует коллаборации"')
        print("\nРЕАЛЬНОСТЬ:")
        print("  1. Premium создаёт задачу")
        print("  2. Система находит релевантного человека")
        print("  3. Рекомендация СОХРАНЯЕТСЯ в профиль этого человека")
        print("  4. Когда человек САМ напишет боту (может через неделю), AI упомянет возможность")
        print("  5. Premium НЕ получает уведомлений о найденных партнёрах")
        print("  6. Никаких автоматических сообщений/инициации контакта")
        
        print("\nЭТО НЕ 'автопилот', это ПАССИВНЫЙ matching через диалог с AI")
        
    finally:
        # Очистка
        session.query(Task).delete()
        session.query(UserProfile).delete()
        session.query(User).delete()
        session.commit()
        session.close()
        print("\n✅ Тестовые данные очищены")


if __name__ == "__main__":
    asyncio.run(test_premium_flow())
