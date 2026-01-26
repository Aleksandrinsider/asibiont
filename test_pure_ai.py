import asyncio
import sys
import os
sys.path.append('.')

from ai_integration.chat import chat_with_ai
from models import Session, User, init_db
from datetime import datetime
import pytz

async def test_pure_ai():
    """Тест чистого AI распознавания без keyword matching"""

    # Инициализируем базу данных
    init_db()

    # Создадим тестового пользователя
    session = Session()
    test_user = session.query(User).filter_by(telegram_id=123456789).first()
    if not test_user:
        test_user = User(telegram_id=123456789, username='test_user')
        session.add(test_user)
        session.commit()

    # Тестовые сообщения для проверки чистого AI
    test_messages = [
        'я только что закончил с проектом',
        'только вернулся с тренировки',
        'готово, сделал отчет',
        'поручи @test_user проверить код',
        'передай @test_user задачу по дизайну',
        'нужно сделать презентацию к завтра',
        'напомни мне позвонить маме'
    ]

    print("=== ТЕСТИРОВАНИЕ ЧИСТОГО AI РАСПОЗНАВАНИЯ ===")
    print("Keyword matching отключен, работает только AI\n")

    for i, msg in enumerate(test_messages, 1):
        print(f'{i}. Тестируем: "{msg}"')
        try:
            result = await chat_with_ai(
                message=msg,
                user_id=test_user.telegram_id,
                db_session=session,
                message_type='user'
            )
            print(f'   Ответ: {result[:150]}...' if len(result) > 150 else f'   Ответ: {result}')
        except Exception as e:
            print(f'   Ошибка: {e}')
        print()

    session.close()
    print("Тестирование завершено!")

if __name__ == "__main__":
    asyncio.run(test_pure_ai())