import asyncio
import sys
sys.path.append('.')
from ai_integration.chat import chat_with_ai
from models import User, Task, SessionLocal

async def test_ai_behavior():
    """Тестируем поведение AI с новыми промптами"""

    # Создаем тестового пользователя
    session = SessionLocal()
    user = session.query(User).filter_by(telegram_id=123456789).first()
    if not user:
        user = User(telegram_id=123456789, username='test_user')
        session.add(user)
        session.commit()

    print("🧪 ТЕСТИРОВАНИЕ AI ПОВЕДЕНИЯ")
    print("=" * 50)

    # Тест 1: Создание задачи из естественного запроса
    print("\n1️⃣ ТЕСТ: Создание задачи из 'нужно проверить почту'")
    test_message = "нужно проверить почту"
    print(f"Запрос: {test_message}")

    try:
        # Очищаем задачи перед тестом
        session.query(Task).filter_by(user_id=user.id).delete()
        session.commit()
        
        result = await chat_with_ai(test_message, user_id=user.telegram_id, db_session=session)
        print(f"Ответ AI: {result[:200]}...")

        # Проверяем создалась ли задача
        tasks = session.query(Task).filter_by(user_id=user.id).filter(Task.status.in_(['active', 'pending'])).all()
        task_titles = [t.title for t in tasks]
        if any('почту' in title.lower() for title in task_titles):
            print("✅ ЗАДАЧА СОЗДАНА: Найдена задача с 'почту'")
        else:
            print("❌ ЗАДАЧА НЕ СОЗДАНА: Нет задачи с упоминанием почты")

    except Exception as e:
        print(f"❌ ОШИБКА: {e}")

    # Тест 2: Проверка отсутствия навязчивых упоминаний контактов
    print("\n2️⃣ ТЕСТ: Отсутствие навязчивых упоминаний контактов")
    test_message = "какие у меня задачи"
    print(f"Запрос: {test_message}")

    try:
        result = await chat_with_ai(test_message, user_id=user.telegram_id, db_session=session)
        print(f"Ответ AI: {result[:200]}...")

        if '@' in result and ('test_user' in result or 'контакт' in result.lower()):
            print("❌ КОНТАКТЫ УПОМЯНУТЫ: AI упоминает контакты без запроса")
        else:
            print("✅ КОНТАКТЫ НЕ УПОМЯНУТЫ: AI не навязывает контакты")

    except Exception as e:
        print(f"❌ ОШИБКА: {e}")

    # Тест 3: Проактивная помощь
    print("\n3️⃣ ТЕСТ: Проактивная помощь при отсутствии задач")
    # Сначала удалим все активные задачи
    session.query(Task).filter_by(user_id=user.id).delete()
    session.commit()

    test_message = "привет"
    print(f"Запрос: {test_message}")

    try:
        result = await chat_with_ai(test_message, user_id=user.telegram_id, db_session=session)
        print(f"Ответ AI: {result[:300]}...")

        # Проверяем предлагает ли AI конкретную помощь
        proactive_keywords = ['давай', 'предлагаю', 'можем', 'помочь', 'запланируем']
        if any(keyword in result.lower() for keyword in proactive_keywords):
            print("✅ ПРОАКТИВНАЯ ПОМОЩЬ: AI предлагает конкретные действия")
        else:
            print("❌ НЕТ ПРОАКТИВНОСТИ: AI просто отвечает без предложений")

    except Exception as e:
        print(f"❌ ОШИБКА: {e}")

    session.close()
    print("\n" + "=" * 50)
    print("🎯 ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")

if __name__ == "__main__":
    asyncio.run(test_ai_behavior())