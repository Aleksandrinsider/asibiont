import sys
sys.path.append('.')
from ai_integration.chat import chat_with_ai
from models import Session, User, UserProfile, SubscriptionTier
import logging
import os

# Устанавливаем локальный режим
os.environ['LOCAL'] = '1'

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Создаем тестового пользователя с PREMIUM подпиской
session = Session()
try:
    # Ищем существующего пользователя или создаем нового
    user = session.query(User).filter_by(telegram_id='999000333999').first()
    if not user:
        user = User(telegram_id='999000333999', username='test_user')
        session.add(user)
        session.commit()

    # Обновляем профиль с городом
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if not profile:
        profile = UserProfile(user_id=user.id, city='Москва', position='программист', skills='Python, машинное обучение', interests='Python, машинное обучение')
        session.add(profile)
    else:
        profile.city = 'Москва'
        profile.position = 'программист'
        profile.skills = 'Python, машинное обучение'
        profile.interests = 'Python, машинное обучение'

    # Устанавливаем PREMIUM подписку
    profile.subscription_tier = SubscriptionTier.PREMIUM
    session.commit()

    print('=== ТЕСТИРОВАНИЕ ИСПОЛЬЗОВАНИЯ ПОГОДЫ И НОВОСТЕЙ В ОТВЕТАХ ===')
    print('Пользователь: PREMIUM, город Москва')
    print()

    # Тестовый вопрос
    test_message = 'Что мне делать сегодня?'
    print(f'Вопрос: {test_message}')
    print()

    # Получаем ответ агента
    response = chat_with_ai(test_message, user.id)

    print('Ответ агента:')
    print(response)
    print()

    # Проверяем, использовал ли агент погоду и новости
    weather_used = 'погода' in response.lower() or 'температура' in response.lower() or '°C' in response or '-20' in response
    news_used = 'новост' in response.lower() or '📰' in response or 'россия' in response.lower()

    print('Анализ использования данных:')
    print(f'✅ Погода использована: {weather_used}')
    print(f'✅ Новости использованы: {news_used}')

    if weather_used and news_used:
        print('🎉 Агент успешно использует и погоду, и новости для персонализации!')
    elif weather_used:
        print('🌤️ Агент использует погоду, но не новости')
    elif news_used:
        print('📰 Агент использует новости, но не погоду')
    else:
        print('❌ Агент не использует ни погоду, ни новости')

finally:
    session.close()