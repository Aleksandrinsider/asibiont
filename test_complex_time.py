import asyncio
from ai_integration.chat import chat_with_ai
from models import Task, User, Session
import logging
logging.basicConfig(level=logging.WARNING)

async def time_complexity_test():
    print('⏰ ТЕСТИРОВАНИЕ СЛОЖНЫХ СЦЕНАРИЕВ С ВРЕМЕНЕМ')
    print('=' * 45)

    # Находим пользователя
    session = Session()
    user = session.query(User).filter_by(telegram_id=12345).first()
    user_db_id = user.id

    # Очистка
    session.query(Task).filter_by(user_id=user_db_id).delete()
    session.commit()

    # === СЛОЖНЫЙ МНОГОХОДОВЫЙ СЦЕНАРИЙ ===
    print('📅 СЦЕНАРИЙ: Планирование рабочего дня')
    print('-' * 35)

    # Шаг 1: Создание нескольких задач на день
    messages = [
        'напомни мне провести утреннюю планерку в 9:00',
        'напомни ответить на важные emails до обеда',
        'напомни сходить на обед в 13:00',
        'напомни подготовить презентацию к 16:00',
        'напомни позвонить клиентам после 17:00',
    ]

    print('\n1️⃣ Создание задач на день:')
    for i, msg in enumerate(messages, 1):
        response = await chat_with_ai(msg, user_id=12345)
        print(f'   {i}. {msg[:30]}... → {response[:40]}...')

    # Проверяем создание
    session = Session()
    tasks_count = session.query(Task).filter_by(user_id=user_db_id, status='pending').count()
    print(f'\nСоздано задач: {tasks_count}')

    # Шаг 2: Просмотр расписания
    print('\n2️⃣ Просмотр расписания:')
    response = await chat_with_ai('покажи мое расписание на сегодня', user_id=12345)
    print(f'Ответ: {response[:100]}...')

    # Шаг 3: Изменения в расписании
    print('\n3️⃣ Изменения в расписании:')
    changes = [
        'перенеси планерку на 9:30',
        'обед теперь в 12:30 вместо 13:00',
        'презентацию нужно сделать к 15:00, а не к 16:00',
    ]

    for change in changes:
        response = await chat_with_ai(change, user_id=12345)
        print(f'   Изменение: {change[:25]}... → {response[:40]}...')

    # Шаг 4: Выполнение задач в течение дня
    print('\n4️⃣ Выполнение задач:')
    completions = [
        'планерка прошла успешно',
        'emails отвечены',
        'обед был вкусным',
    ]

    for completion in completions:
        response = await chat_with_ai(completion, user_id=12345)
        print(f'   Завершено: {completion[:20]}... → {response[:40]}...')

    # Шаг 5: Финальный статус
    print('\n5️⃣ Финальный статус дня:')
    response = await chat_with_ai('что осталось сделать сегодня', user_id=12345)
    print(f'Ответ: {response[:100]}...')

    # === ПРОВЕРКА ВРЕМЕНИ ===
    print('\n⏱️ ПРОВЕРКА ВРЕМЕННЫХ НАСТРОЕК')
    print('-' * 30)

    session = Session()
    tasks = session.query(Task).filter_by(user_id=user_db_id).order_by(Task.reminder_time).all()

    print('Все задачи с временем:')
    for task in tasks:
        status_icon = '✅' if task.status == 'completed' else '⏳'
        time_str = task.reminder_time.strftime('%H:%M') if task.reminder_time else 'без времени'
        print(f'  {status_icon} {task.title[:30]}... | {time_str} | {task.status}')

    # === ТЕСТ ДЛИТЕЛЬНЫХ СЕССИЙ ===
    print('\n🔄 ТЕСТ ДЛИТЕЛЬНЫХ СЕССИЙ')
    print('-' * 25)

    # Создаем задачу и затем через несколько "сообщений" работаем с ней
    await chat_with_ai('напомни написать статью для блога', user_id=12345)

    session_messages = [
        'расскажи подробнее про эту задачу',
        'перенеси ее на следующую неделю',
        'добавь описание: написать про AI технологии',
        'теперь заверши ее',
    ]

    print('Многоходовая работа с одной задачей:')
    for msg in session_messages:
        response = await chat_with_ai(msg, user_id=12345)
        print(f'  "{msg}" → {response[:50]}...')

    # Финальная статистика
    session = Session()
    final_pending = session.query(Task).filter_by(user_id=user_db_id, status='pending').count()
    final_completed = session.query(Task).filter_by(user_id=user_db_id, status='completed').count()

    print(f'\n📊 ИТОГО: {final_pending} активных, {final_completed} завершенных задач')
    print('\n✅ ТЕСТИРОВАНИЕ СЛОЖНЫХ СЦЕНАРИЕВ ЗАВЕРШЕНО!')

if __name__ == "__main__":
    asyncio.run(time_complexity_test())