import asyncio
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
import aiohttp_jinja2
import jinja2
import aioredis
import aiohttp_session
from aiohttp_session import get_session, SimpleCookieStorage
from config import TELEGRAM_TOKEN, WEBHOOK_URL, TELEGRAM_BOT_USERNAME
from handlers import router
from reminder_service import ReminderService
from ai_integration import AIIntegration, chat_with_ai
from models import Base, engine, Session, Subscription, User, Task, UserProfile, Interaction
import os
import datetime
import pytz
from datetime import timedelta
import hashlib
import hmac
import urllib.parse

def check_telegram_authentication(data):
    # Проверка авторизации от Telegram
    token = TELEGRAM_TOKEN
    if token.startswith('bot'):
        token = token[3:]  # Remove 'bot' prefix
    secret_key = hashlib.sha256(token.encode()).digest()
    data_check_string = '\n'.join(sorted([f'{k}={v}' for k, v in data.items() if k != 'hash']))
    hash_computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return hash_computed == data.get('hash')


@aiohttp_jinja2.template('login.html')
async def login_handler(request):
    local = os.getenv("LOCAL") == "1"
    return {'bot_username': TELEGRAM_BOT_USERNAME, 'auth_url': '/auth', 'local': local}


# Temporary simple handler
async def simple_login_handler(request):
    return web.Response(text="Login page - Telegram auth available at /tg_auth")


async def auth_handler(request):
    data = request.query
    if check_telegram_authentication(data):
        user_id = int(data['id'])
        session = await get_session(request)
        session['user_id'] = user_id
        return web.HTTPFound('/dashboard')
    else:
        return web.Response(text='Authentication failed', status=401)


async def test_login_handler(request):
    # Тестовый вход для локального режима
    session = await get_session(request)
    session['user_id'] = 123456789  # Тестовый user_id
    return web.HTTPFound('/dashboard')


async def logout_handler(request):
    session = await get_session(request)
    session.clear()
    return web.HTTPFound('/')


@aiohttp_jinja2.template('dashboard.html')
async def dashboard_handler(request):
    session = await get_session(request)
    user_id = session.get('user_id')
    if not user_id:
        return web.HTTPFound('/')
    # Получить задачи пользователя
    session_db = Session()
    tasks = session_db.query(Task).filter_by(user_id=user_id).all()
    user = session_db.query(User).filter_by(telegram_id=user_id).first()
    profile = session_db.query(UserProfile).filter_by(user_id=user.id).first() if user else None
    session_db.close()
    return {'tasks': tasks, 'user': user, 'profile': profile}


async def tasks_handler(request):
    return web.HTTPFound('/dashboard')


async def profile_handler(request):
    return web.HTTPFound('/dashboard')


async def chat_handler(request):
    session = await get_session(request)
    user_id = session.get('user_id')
    if not user_id:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    data = await request.json()
    message = data.get('message', '')

    # Get AI response
    response = await chat_with_ai(message, user_id=user_id)

    return web.json_response({'response': response})


async def yookassa_webhook(request):
    # Заглушка для webhook Yookassa
    return web.Response(text='OK')


bot = Bot(token=TELEGRAM_TOKEN)


async def send_reminder(task_id, user_id):
    session = Session()
    task = session.query(Task).filter_by(id=task_id).first()
    session.close()
    if task and task.status == 'pending':
        await bot.send_message(user_id, f"Напоминание: {task.title}")


async def schedule_reminders(scheduler):
    try:
        session = Session()
        tasks = session.query(Task).filter(Task.reminder_time.isnot(None), Task.status == 'pending').all()
        session.close()
        for task in tasks:
            if task.reminder_time.tzinfo is None:
                task.reminder_time = task.reminder_time.replace(tzinfo=pytz.UTC)
            if task.reminder_time > datetime.now(pytz.UTC):
                scheduler.add_job(send_reminder, 'date', run_date=task.reminder_time, args=[task.id, task.user_id])
    except Exception as e:
        print(f"Error in schedule_reminders: {e}")


async def on_startup(bot: Bot):
    print("Starting on_startup")
    if os.getenv("LOCAL") == "1":
        await bot.delete_webhook()
        print("Webhook deleted for local mode")
    else:
        try:
            await bot.set_webhook(WEBHOOK_URL)
            print(f"Webhook set to: {WEBHOOK_URL}")
        except Exception as e:
            print(f"Error setting webhook: {e}")
    # Инициализировать AI и ReminderService
    ai_service = AIIntegration()
    reminder_service = ReminderService(bot, ai_service)
    await reminder_service.start()
    print("ReminderService started")


async def main():
    print("Starting main function")
    # Создание таблиц
    Base.metadata.create_all(engine)
    print("Database tables created")

    dp = Dispatcher()
    dp.include_router(router)
    print("Dispatcher created and router included")

    # Проверка на локальный запуск
    if os.getenv("LOCAL") == "1":
        # Локальный запуск с polling и веб-сервером
        print("Запуск в локальном режиме (polling + web)...")
        app = web.Application()
        
        # Setup Jinja2
        aiohttp_jinja2.setup(app, loader=jinja2.FileSystemLoader('templates'))
        
        # Setup sessions
        storage = SimpleCookieStorage()
        aiohttp_session.setup(app, storage)
        
        # Web app routes
        app.router.add_get('/', login_handler)
        app.router.add_get('/telegram_auth', auth_handler)
        app.router.add_get('/test_login', test_login_handler)
        app.router.add_get('/logout', logout_handler)
        app.router.add_get('/dashboard', dashboard_handler)
        app.router.add_get('/tasks', tasks_handler)
        app.router.add_get('/profile', profile_handler)
        app.router.add_post('/chat', chat_handler)
        app.router.add_static('/static', 'static')
        
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, 'localhost', 8080)
        await site.start()
        print("Web server started on http://localhost:8080")
        
        await on_startup(bot)
        
        # Для локального тестирования веб, polling отключен
        # await dp.start_polling(bot)
        print("Polling disabled for local web testing. Press Ctrl+C to stop.")
        # Бесконечный цикл для поддержания сервера
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("Stopping server...")
            await runner.cleanup()
    else:
        # Вебхук для Railway
        print("Setting up webhook for Railway")
        app = web.Application()
        
        # Setup Jinja2
        aiohttp_jinja2.setup(app, loader=jinja2.FileSystemLoader('templates'))
        
        # Setup sessions
        storage = SimpleCookieStorage()
        aiohttp_session.setup(app, storage)
        
        webhook_requests_handler = SimpleRequestHandler(
            dispatcher=dp,
            bot=bot,
        )
        webhook_requests_handler.register(app, path="/webhook")

        setup_application(app, dp, bot=bot)

        app.router.add_post('/yookassa-webhook', yookassa_webhook)

        # Web app routes
        app.router.add_get('/', login_handler)
        app.router.add_get('/telegram_auth', auth_handler)
        app.router.add_get('/test_login', test_login_handler)
        app.router.add_get('/logout', logout_handler)
        app.router.add_get('/dashboard', dashboard_handler)
        app.router.add_get('/tasks', tasks_handler)
        app.router.add_get('/profile', profile_handler)
        app.router.add_post('/chat', chat_handler)
        app.router.add_static('/static', 'static')

        app.router.add_post('/yookassa-webhook', yookassa_webhook)

        print("Calling on_startup")

        print("Calling on_startup")
        await on_startup(bot)

        runner = web.AppRunner(app)
        await runner.setup()
        port_env = os.getenv("PORT")
        print(f"PORT env var: {port_env}")
        port = int(port_env) if port_env else 8000
        print(f"Using port: {port}")
        print(f"Starting server on port {port}")
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        print(f"Server started on port {port}")

        print(f"Бот запущен в режиме вебхуков на порту {port}!")

        # Keep the event loop running
        try:
            await asyncio.sleep(float('inf'))
        except KeyboardInterrupt:
            print("Shutting down...")
        finally:
            await runner.cleanup()


async def yookassa_webhook(request):
    data = await request.json()
    if data.get('event') == 'payment.succeeded':
        payment = data['object']
        user_id = payment['metadata']['user_id']
        session = Session()
        user = session.query(User).filter_by(telegram_id=int(user_id)).first()
        if user:
            subscription = session.query(Subscription).filter_by(user_id=user.id).first()
            if not subscription:
                subscription = Subscription(user_id=user.id)
                session.add(subscription)
            subscription.status = 'active'
            subscription.start_date = datetime.now(pytz.UTC)
            subscription.end_date = datetime.now(pytz.UTC) + timedelta(days=30)  # Месяц
            session.commit()
            await bot.send_message(int(user_id), "Подписка активирована! Теперь у вас доступ ко всем премиум-функциям.")
        session.close()
    return web.Response(text="OK")

if __name__ == "__main__":
    print("Running main")
    asyncio.run(main())
