"""Тест проактивного предложения релевантных контактов"""
import asyncio
import sys
import os
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ai_integration.chat import chat_with_ai
from models import Session, User, UserProfile, Base, engine, Task
import pytz

async def test_contacts_recommendations():
    """Тестирует проактивное предложение релевантных контактов"""
    Base.metadata.create_all(bind=engine)
    
    session = Session()
    try:
        # Создаём тестового пользователя
        user = session.query(User).filter_by(telegram_id=777777).first()
        if not user:
            user = User(telegram_id=777777, username="test_contacts_user")
            session.add(user)
            session.flush()
        
        # Профиль с явными интересами для нетворкинга
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            profile = UserProfile(
                user_id=user.id,
                company="TechStartup.AI",
                position="Основатель",
                interests="ИИ, стартапы, Python, нетворкинг, партнерства",
                goals="Найти технического со-основателя. Привлечь инвестиции. Расширить сеть контактов.",
                city="Москва"
            )
            session.add(profile)
        else:
            profile.company = "TechStartup.AI"
            profile.position = "Основатель"
            profile.interests = "ИИ, стартапы, Python, нетворкинг, партнерства"
            profile.goals = "Найти технического со-основателя. Привлечь инвестиции. Расширить сеть контактов."
            profile.city = "Москва"
        
        session.commit()
        
        # Тест 1: Обсуждение проекта - должен предложить контакты
        print("=" * 80)
        print("[ТЕСТ 1] Вопрос: 'Как продвигать стартап?'")
        print("Ожидание: агент должен ПРОАКТИВНО предложить релевантных партнеров")
        print("=" * 80)
        print()
        
        response1 = await chat_with_ai(
            message="Как продвигать стартап?",
            user_id=777777
        )
        
        print(f"[BOT ОТВЕТ]:\n{response1.get('response', '')}\n")
        
        # Анализ упоминания контактов
        resp_text = response1.get('response', '')
        tool_calls = response1.get('tool_calls', [])
        
        used_find_partners = any('find_partners' in str(call) for call in tool_calls)
        mentions_contacts = any(word in resp_text.lower() for word in ['партнер', 'контакт', '@', 'найду', 'людей', 'со-основател'])
        
        print("\n📊 АНАЛИЗ РЕКОМЕНДАЦИИ КОНТАКТОВ:")
        if used_find_partners:
            print("   ✅ Использовал find_partners")
        else:
            print("   ❌ НЕ использовал find_partners")
        
        if mentions_contacts:
            print("   ✅ Упомянул контакты/партнеров")
        else:
            print("   ❌ НЕ упомянул контакты/партнеров")
        
        if used_find_partners or mentions_contacts:
            print("\n✅ ХОРОШО - агент предлагает контакты")
        else:
            print("\n❌ ПЛОХО - агент игнорирует возможность предложить контакты")
        
        print("\n" + "=" * 80)
        
        # Тест 2: Обсуждение поиска со-основателя
        print("\n[ТЕСТ 2] Вопрос: 'Нужен технический со-основатель'")
        print("Ожидание: агент ОБЯЗАН использовать find_partners")
        print("=" * 80)
        print()
        
        response2 = await chat_with_ai(
            message="Нужен технический со-основатель",
            user_id=777777
        )
        
        print(f"[BOT ОТВЕТ]:\n{response2.get('response', '')}\n")
        
        tool_calls2 = response2.get('tool_calls', [])
        used_find_partners2 = any('find_partners' in str(call) for call in tool_calls2)
        
        print("\n📊 АНАЛИЗ:")
        if used_find_partners2:
            print("   ✅ Использовал find_partners - ПРАВИЛЬНО!")
        else:
            print("   ❌ НЕ использовал find_partners - ОШИБКА!")
            print("   Агент должен был найти релевантных людей!")
        
        print("\n" + "=" * 80)
        
        # Тест 3: Приветствие - должен ли проактивно предлагать контакты?
        print("\n[ТЕСТ 3] Простое приветствие: 'Привет'")
        print("Ожидание: агент может упомянуть возможность нетворкинга (т.к. цель в профиле)")
        print("=" * 80)
        print()
        
        response3 = await chat_with_ai(
            message="Привет",
            user_id=777777
        )
        
        print(f"[BOT ОТВЕТ]:\n{response3.get('response', '')}\n")
        
        resp_text3 = response3.get('response', '')
        mentions_networking = any(word in resp_text3.lower() for word in ['партнер', 'контакт', 'со-основател', 'нетворкинг', 'связ'])
        
        print("\n📊 АНАЛИЗ:")
        if mentions_networking:
            print("   ✅ Упомянул нетворкинг/контакты с учетом целей в профиле")
            print("   Агент использует цель 'Найти со-основателя' проактивно!")
        else:
            print("   ⚠️ НЕ упомянул нетворкинг")
            print("   Можно было упомянуть цель 'Найти со-основателя' естественно")
        
        print("\n" + "=" * 80)
        
        # Итоговая оценка
        print("\n" + "=" * 80)
        print("ИТОГОВАЯ ОЦЕНКА:")
        print("=" * 80)
        
        score = 0
        if used_find_partners or mentions_contacts:
            score += 1
            print("✅ Тест 1: Предлагает контакты при обсуждении продвижения")
        else:
            print("❌ Тест 1: НЕ предлагает контакты")
        
        if used_find_partners2:
            score += 1
            print("✅ Тест 2: Использует find_partners при явном запросе")
        else:
            print("❌ Тест 2: НЕ использует find_partners при явном запросе")
        
        if mentions_networking:
            score += 1
            print("✅ Тест 3: Упоминает нетворкинг в приветствиях")
        else:
            print("⚠️ Тест 3: НЕ упоминает (может быть OK для естественности)")
        
        print(f"\n🎯 ИТОГО: {score}/3 ({int(score/3*100)}%)")
        
        if score == 3:
            print("✅ ОТЛИЧНО - агент проактивно предлагает релевантные контакты!")
        elif score == 2:
            print("⚠️ ХОРОШО - но можно усилить проактивность")
        else:
            print("❌ ПЛОХО - агент недостаточно использует возможности нетворкинга")
        
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_contacts_recommendations())
