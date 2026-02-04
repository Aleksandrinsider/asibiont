import logging
logging.basicConfig(level=logging.INFO)
from ai_integration.commands.conversation import ConversationCommand
from ai_integration.router import CommandRouter
from models import User, SessionLocal
import pytz
from datetime import datetime
import asyncio

async def comprehensive_test():
    # Создаем тестового пользователя
    user = User(
        telegram_id=12345,
        username='test_user',
        timezone='Europe/Moscow',
        created_at=datetime.now(pytz.UTC)
    )

    # Создаем сессию БД
    db_session = SessionLocal()

    # Создаем роутер
    router = CommandRouter()

    message_time = datetime.now(pytz.UTC)

    test_cases = [
        ('привет', 'Простое приветствие'),
        ('Привет!', 'Приветствие с восклицательным знаком'),
        ('hello', 'Английское приветствие'),
        ('здравствуй', 'Русское приветствие'),
        ('добрый день', 'Приветствие с днем'),
        ('hi there', 'Приветствие с дополнением'),
        ('какое сейчас время', 'Вопрос о времени'),
        ('сколько времени', 'Вопрос о времени (короткий)'),
        ('какой сегодня день', 'Вопрос о дате'),
        ('что ты умеешь', 'Вопрос о возможностях'),
        ('расскажи о себе', 'Вопрос о боте'),
        ('помоги с задачей', 'Просьба о помощи'),
        ('погода в москве', 'Вопрос о погоде'),
        ('новости сегодня', 'Вопрос о новостях'),
    ]

    for message, description in test_cases:
        print(f'\n=== {description}: \"{message}\" ===')
        try:
            result = await router.route(message, user.telegram_id, message_time)
            response = await result.execute(user, db_session)
            print(f'Ответ: {response}')

            # Проверки на галлюцинации
            hallucinations = []
            # Проверяем на выдуманные имена контактов
            contact_names = ['иван', 'мария', 'алексей', 'ольга', 'дмитрий', 'анна', 'сергей', 'елена']
            if any(name in response.lower() for name in contact_names):
                hallucinations.append('выдуманные контакты')
            # Проверяем на выдуманный профиль (несколько интересов вместе)
            if ('технологии' in response.lower() or 'бизнес' in response.lower() or 'спорт' in response.lower()) and ('интересуешься' in response.lower() or 'увлекаешься' in response.lower()):
                hallucinations.append('выдуманный профиль')
            # Проверяем на выдуманные задачи
            if 'активных задач' in response.lower() and ('у тебя' in response.lower() or 'нет задач' in response.lower()):
                hallucinations.append('выдуманные задачи')
            # Проверяем на запрещенные описания времени
            if 'середина дня' in response.lower() or 'отличное время для' in response.lower() or 'хорошее время' in response.lower():
                hallucinations.append('описание времени дня')
            # Проверяем на слишком длинные ответы для простых вопросов
            if len(response.split()) > 30 and any(word in message.lower() for word in ['привет', 'здравствуй', 'hello', 'hi']):
                hallucinations.append('слишком длинный ответ на приветствие')

            if hallucinations:
                print(f'❌ ГАЛЛЮЦИНАЦИИ: {hallucinations}')
            else:
                print('✅ Без галлюцинаций')

        except Exception as e:
            print(f'❌ Ошибка: {e}')

    db_session.close()

if __name__ == "__main__":
    asyncio.run(comprehensive_test())