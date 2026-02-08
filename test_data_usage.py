"""Тест использования всех доступных данных для продуктивности"""
import asyncio
import sys
import os
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ai_integration.chat import chat_with_ai
from models import Session, User, UserProfile, Base, engine, Task
import pytz

async def test_data_usage():
    """Тестирует как агент использует все доступные данные"""
    Base.metadata.create_all(bind=engine)
    
    session = Session()
    try:
        # Создаём тестового пользователя с полным профилем
        user = session.query(User).filter_by(telegram_id=888888).first()
        if not user:
            user = User(telegram_id=888888, username="test_data_user")
            session.add(user)
            session.flush()
        
        # Богатый профиль
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            profile = UserProfile(
                user_id=user.id,
                company="ASI Biont",
                position="Основатель",
                interests="ИИ, стартапы, бизнес, Python",
                goals="Привлечь 100 пользователей за месяц. Запустить реферальную программу. Выучить Python для доработки бота.",
                city="Москва"
            )
            session.add(profile)
        else:
            profile.company = "ASI Biont"
            profile.position = "Основатель"
            profile.interests = "ИИ, стартапы, бизнес, Python"
            profile.goals = "Привлечь 100 пользователей за месяц. Запустить реферальную программу. Выучить Python для доработки бота."
            profile.city = "Москва"
        
        # Добавим задачи: активную, просроченную и будущую
        session.query(Task).filter_by(user_id=user.id).delete()
        
        moscow_tz = pytz.timezone('Europe/Moscow')
        now = datetime.now(moscow_tz)
        
        # Просроченная задача
        overdue_task = Task(
            user_id=user.id,
            title="Написать пост на Habr про ASI Biont",
            description="Технический разбор AI-агента",
            due_date=(now - timedelta(days=1)).replace(tzinfo=None),
            status="pending"
        )
        session.add(overdue_task)
        
        # Задача на сегодня
        today_task = Task(
            user_id=user.id,
            title="Созвон с потенциальным партнером",
            description="Обсудить интеграцию",
            due_date=now.replace(hour=18, minute=0, second=0, microsecond=0, tzinfo=None),
            status="pending"
        )
        session.add(today_task)
        
        # Будущая задача
        future_task = Task(
            user_id=user.id,
            title="Урок 1-3 Python на Stepik",
            description="Начать обучение",
            due_date=(now + timedelta(days=2)).replace(hour=10, minute=0, tzinfo=None),
            status="pending"
        )
        session.add(future_task)
        
        session.commit()
        
        # Тест 1: Общий вопрос - должен использовать ВСЕ данные
        print("=" * 80)
        print("[ТЕСТ 1] Вопрос: 'Что делать дальше с проектом?'")
        print("Ожидание: агент должен упомянуть проект, цели, задачи, интересы")
        print("=" * 80)
        print()
        
        response1 = await chat_with_ai(
            message="Что делать дальше с проектом?",
            user_id=888888
        )
        
        print(f"[BOT ОТВЕТ]:\n{response1.get('response', '')}\n")
        
        # Анализ как агент использовал данные
        resp_text = response1.get('response', '')
        usage_score = []
        
        if "ASI Biont" in resp_text or "проект" in resp_text.lower():
            usage_score.append("✅ Проект упомянут")
        else:
            usage_score.append("❌ Проект НЕ упомянут")
        
        if "100 пользователей" in resp_text or "пользователей" in resp_text:
            usage_score.append("✅ Цель упомянута")
        else:
            usage_score.append("❌ Цель НЕ упомянута")
        
        if "Habr" in resp_text or "пост" in resp_text.lower() or "просроч" in resp_text.lower():
            usage_score.append("✅ Просроченная задача учтена") 
        else:
            usage_score.append("❌ Просроченная задача НЕ учтена")
        
        if "созвон" in resp_text.lower() or "партнер" in resp_text.lower():
            usage_score.append("✅ Задача на сегодня учтена")
        else:
            usage_score.append("❌ Задача на сегодня НЕ учтена")
        
        if "Python" in resp_text:
            usage_score.append("✅ Интересы/цели (Python) учтены")
        else:
            usage_score.append("❌ Интересы (Python) НЕ учтены")
        
        print("\n📊 АНАЛИЗ ИСПОЛЬЗОВАНИЯ ДАННЫХ:")
        for item in usage_score:
            print(f"   {item}")
        
        score = sum(1 for item in usage_score if "✅" in item)
        print(f"\n🎯 ИТОГО: {score}/5 ({score*20}%)")
        
        if score >= 4:
            print("✅ ОТЛИЧНО - агент использует ВСЕ доступные данные!")
        elif score >= 3:
            print("⚠️ ХОРОШО - но можно использовать больше данных")
        else:
            print("❌ ПЛОХО - агент игнорирует большую часть данных")
        
        print("\n" + "=" * 80)
        
        # Тест 2: "Устал" - должен предложить конкретную помощь на основе данных
        print("\n[ТЕСТ 2] Вопрос: 'Устал'")
        print("Ожидание: агент должен предложить конкретные действия с учетом задач")
        print("=" * 80)
        print()
        
        response2 = await chat_with_ai(
            message="Устал",
            user_id=888888
        )
        
        print(f"[BOT ОТВЕТ]:\n{response2.get('response', '')}\n")
        
        resp_text2 = response2.get('response', '')
        if "задач" in resp_text2.lower() or "Habr" in resp_text2 or "созвон" in resp_text2.lower():
            print("✅ Агент учитывает задачи при ответе на 'Устал'")
        else:
            print("❌ Агент НЕ использует контекст задач")
        
        print("\n" + "=" * 80)
        
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_data_usage())
