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
from datetime import datetime
from handlers import router
from reminder_service import ReminderService
from ai_integration import AIIntegration, chat_with_ai, get_partners_list
from models import Base, engine, Session, Subscription, User, Task, UserProfile, Interaction
import os
import pytz
from datetime import timedelta
import hashlib
import hmac
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    return web.HTTPFound('/dashboard')


# Temporary simple handler
async def simple_login_handler(request):
    return web.Response(text="Login page - Telegram auth available at /tg_auth")


async def auth_handler(request):
    data = request.query
    if check_telegram_authentication(data):
        user_id = int(data['id'])
        session_db = Session()
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            user = User(telegram_id=user_id, username=data.get('username'), first_name=data.get('first_name'))
            session_db.add(user)
            session_db.commit()
        
        session_db.close()
        
        session = await get_session(request)
        session['user_id'] = user_id
        return web.HTTPFound('/dashboard')
    else:
        return web.Response(text='Authentication failed', status=401)


async def test_login_handler(request):
    # Тестовый вход для локального режима
    session = await get_session(request)
    session['user_id'] = 123456789  # Тестовый user_id
    
    # Создать тестового пользователя, если не существует
    session_db = Session()
    user = session_db.query(User).filter_by(telegram_id=123456789).first()
    if not user:
        user = User(telegram_id=123456789, username='test_user')
        session_db.add(user)
        session_db.commit()
    
    # Создать тестовую подписку для локального тестирования
    subscription = session_db.query(Subscription).filter_by(user_id=user.id).first()
    if not subscription:
        from datetime import datetime, timedelta
        end_date = datetime.now() + timedelta(days=30)  # Подписка на 30 дней
        subscription = Subscription(user_id=user.id, status='active', plan='monthly', end_date=end_date)
        session_db.add(subscription)
        session_db.commit()
    
    session_db.close()
    
    return web.HTTPFound('/dashboard')


async def logout_handler(request):
    session = await get_session(request)
    session.clear()
    return web.HTTPFound('/')


@aiohttp_jinja2.template('dashboard_new.html')
async def dashboard_handler(request):
    session = await get_session(request)
    user_id = session.get('user_id')
    
    logged_in = bool(user_id)
    
    if not logged_in:
        return {
            'logged_in': False,
            'current_date': '',
            'current_time': '',
            'formatted_end_date': None,
            'is_local': os.getenv('LOCAL') == '1'
        }
    
    # Получить задачи пользователя
    session_db = Session()
    user = session_db.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        session_db.close()
        return {
            'logged_in': False,
            'current_date': '',
            'current_time': '',
            'formatted_end_date': None
        }
    
    # Проверить подписку
    subscription = session_db.query(Subscription).filter_by(user_id=user.id).first()
    if not subscription or subscription.status != 'active':
        session_db.close()
        return aiohttp_jinja2.render_template('no_subscription.html', request, {'bot_username': TELEGRAM_BOT_USERNAME})
    
    tasks = session_db.query(Task).filter_by(user_id=user.id).all()
    profile = session_db.query(UserProfile).filter_by(user_id=user.id).first() if user else None
    interactions = session_db.query(Interaction).filter_by(user_id=user.id).order_by(Interaction.created_at.desc()).limit(10).all() if user else []
    subscription = session_db.query(Subscription).filter_by(user_id=user.id).first() if user else None
    partners = get_partners_list(user_id=user_id)
    # Add common interests
    if profile and profile.interests:
        user_interests = set(i.strip().lower() for i in profile.interests.split(','))
        for p in partners:
            if hasattr(p, 'interests') and p.interests:
                partner_interests = set(i.strip().lower() for i in p.interests.split(','))
                common = user_interests & partner_interests
                p.common_interests = ', '.join(common) if common else 'Нет общих интересов'
            else:
                p.common_interests = 'Интересы не указаны'
    session_db.close()
    
    # Calculate metrics
    total_tasks = len(tasks)
    completed_tasks = len([t for t in tasks if t.status == 'completed'])
    pending_tasks = len([t for t in tasks if t.status == 'pending'])
    skipped_tasks = len([t for t in tasks if t.status == 'skipped'])
    
    # Format date and time in user's timezone
    base_now = datetime.now(pytz.UTC)
    user_now = base_now
    if user and user.timezone:
        try:
            user_tz = pytz.timezone(user.timezone)
            user_now = base_now.astimezone(user_tz)
        except pytz.exceptions.UnknownTimeZoneError:
            user_now = base_now
    months = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
    current_date = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
    current_time = user_now.strftime('%H:%M')
    
    # Format subscription end date
    formatted_end_date = None
    if subscription and subscription.end_date:
        end_dt = subscription.end_date
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=pytz.UTC)
        end_local = end_dt.astimezone(user_tz if user.timezone else pytz.UTC)
        formatted_end_date = f"{end_local.day} {months[end_local.month - 1]} {end_local.year}"
    
    # Calculate upcoming reminders
    upcoming_reminders = []
    if user:
        for task in tasks:
            if task.reminder_time:
                if task.reminder_time.tzinfo is None:
                    task.reminder_time = task.reminder_time.replace(tzinfo=pytz.UTC)
                if task.reminder_time.astimezone(user_tz if user.timezone else pytz.UTC) > user_now and task.status == 'pending':
                    reminder_time_local = task.reminder_time.astimezone(user_tz if user.timezone else pytz.UTC).strftime("%H:%M")
                    upcoming_reminders.append(f"{task.title} в {reminder_time_local}")
    
    return {
        'logged_in': True,
        'tasks': tasks, 
        'user': user, 
        'profile': profile,
        'interactions': interactions,
        'partners': partners,
        'subscription': subscription,
        'total_tasks': total_tasks,
        'completed_tasks': completed_tasks,
        'pending_tasks': pending_tasks,
        'skipped_tasks': skipped_tasks,
        'current_date': current_date,
        'current_time': current_time,
        'formatted_end_date': formatted_end_date,
        'upcoming_reminders': upcoming_reminders[:5],  # Limit to 5
        'is_local': os.getenv('LOCAL') == '1'
    }


async def tasks_handler(request):
    return web.HTTPFound('/dashboard')


async def profile_handler(request):
    return web.HTTPFound('/dashboard')


async def chat_handler(request):
    session = await get_session(request)
    user_id = session.get('user_id')
    print("Chat handler called, session user_id:", user_id)
    
    # For local demo, auto-login
    if os.getenv("LOCAL") == "1" and not user_id:
        user_id = 123456789
        print("Set user_id to demo:", user_id)
    
    if os.getenv("LOCAL") != "1" and not user_id:
        print("No user_id, returning 401")
        return web.json_response({'error': 'Not authenticated'}, status=401)

    data = await request.json()
    message = data.get('message', '')
    print("Message received:", message)

    # Save user message
    session_db = Session()
    user = session_db.query(User).filter_by(telegram_id=user_id).first()
    print("User found:", user is not None)
    if user:
        interaction_user = Interaction(user_id=user.id, message_type='user', content=message)
        session_db.add(interaction_user)
        session_db.commit()

    # Get AI response
    response = await chat_with_ai(message, user_id=user_id)

    # Save agent response
    if user:
        interaction_agent = Interaction(user_id=user.id, message_type='agent', content=response)
        session_db.add(interaction_agent)
        session_db.commit()

    session_db.close()

    return web.json_response({'response': response})


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


async def on_startup(app):
    logger.info("Starting on_startup")
    if os.getenv("LOCAL") == "1":
        await bot.delete_webhook()
        logger.info("Webhook deleted for local mode")
    else:
        try:
            await bot.set_webhook(WEBHOOK_URL)
            logger.info(f"Webhook set to: {WEBHOOK_URL}")
        except Exception as e:
            logger.error(f"Error setting webhook: {e}")
    # Инициализировать AI и ReminderService
    logger.info("Initializing AI and ReminderService")
    ai_service = AIIntegration()
    reminder_service = ReminderService(bot, ai_service)
    await reminder_service.start()
    logger.info("ReminderService started")
    
    # Create demo data for local mode
    if os.getenv("LOCAL") == "1":
        logger.info("Creating demo data")
        create_demo_data()
        logger.info("Demo data created")


def create_demo_data():
    session = Session()
    try:
        # Check if demo user exists
        demo_user = session.query(User).filter_by(telegram_id=123456789).first()
        if not demo_user:
            demo_user = User(telegram_id=123456789, username='demo_user')
            session.add(demo_user)
            session.commit()
        
        # Create subscription
        subscription = session.query(Subscription).filter_by(user_id=demo_user.id).first()
        if not subscription:
            subscription = Subscription(
                user_id=demo_user.id, 
                status='active', 
                plan='monthly', 
                start_date=datetime.now(pytz.UTC),
                end_date=datetime.now(pytz.UTC) + timedelta(days=30)
            )
            session.add(subscription)
            session.commit()
        
        # Create profile
        profile = session.query(UserProfile).filter_by(user_id=demo_user.id).first()
        if profile:
            session.delete(profile)
            session.commit()
        profile = UserProfile(
            user_id=demo_user.id,
            skills='Python, ИИ, Веб-разработка',
            interests='Машинное обучение, Открытый исходный код, Технологические стартапы',
            goals='Создать успешный AI-продукт, найти партнеров для коллаборации',
            city='Москва',
            current_plans='Разработка TaskChat, изучение новых фреймворков',
            total_tasks_created=15,
            completed_tasks=12,
            skipped_tasks=1,
            average_completion_time=45
        )
        session.add(profile)
        session.commit()
        
        # Create tasks
        if not session.query(Task).filter_by(user_id=demo_user.id).first():
            tasks_data = [
                {
                    'title': 'Разработать AI-ассистента для задач',
                    'description': 'Создать чат-бот с функциями управления задачами и поиска партнеров',
                    'status': 'completed',
                    'reminder_time': datetime.now(pytz.UTC) + timedelta(hours=2)
                },
                {
                    'title': 'Добавить систему напоминаний',
                    'description': 'Интегрировать напоминания с Telegram ботом',
                    'status': 'completed',
                    'reminder_time': None
                },
                {
                    'title': 'Создать демо-данные',
                    'description': 'Заполнить базу тестовыми данными для демонстрации',
                    'status': 'in_progress',
                    'reminder_time': datetime.now(pytz.UTC) + timedelta(days=1)
                },
                {
                    'title': 'Оптимизировать UI для мобильных',
                    'description': 'Сделать интерфейс адаптивным для всех устройств',
                    'status': 'pending',
                    'reminder_time': datetime.now(pytz.UTC) + timedelta(days=2)
                }
            ]
            for task_data in tasks_data:
                task = Task(user_id=demo_user.id, **task_data)
                session.add(task)
            session.commit()
        
        # Create partners
        if not session.query(UserProfile).filter(UserProfile.user_id != demo_user.id).first():
            partners_data = [
                {
                    'contact_info': 'partner1',
                    'skills': 'React, Node.js, UI/UX',
                    'interests': 'Веб-разработка, Дизайн, Стартапы',
                    'goals': 'Создать крутой веб-приложение',
                    'city': 'Москва',
                    'current_plans': 'Ищу команду для проекта'
                },
                {
                    'contact_info': 'partner2', 
                    'skills': 'Data Science, Python, ML',
                    'interests': 'ИИ, Большие данные, Исследования',
                    'goals': 'Применить ML в реальных проектах',
                    'city': 'Санкт-Петербург',
                    'current_plans': 'Работаю над исследовательским проектом'
                },
                {
                    'contact_info': 'partner3',
                    'skills': 'DevOps, Cloud, Kubernetes',
                    'interests': 'Инфраструктура, Автоматизация, Открытый исходный код',
                    'goals': 'Строить надежные системы',
                    'city': 'Екатеринбург',
                    'current_plans': 'Миграция в облако'
                }
            ]
            for partner_data in partners_data:
                partner_user = User(telegram_id=100000000 + len(partners_data), username=partner_data['contact_info'])
                session.add(partner_user)
                session.commit()
                partner_profile = UserProfile(user_id=partner_user.id, **partner_data)
                session.add(partner_profile)
            session.commit()
        
        # Create interactions
        if not session.query(Interaction).filter_by(user_id=demo_user.id).first():
            interactions_data = [
                ('user', 'Привет! Расскажи о себе'),
                ('agent', 'Привет! Я AI-ассистент TaskChat. Помогаю управлять задачами и находить партнеров для проектов. Что вас интересует?'),
                ('user', 'Какие у меня задачи?'),
                ('agent', 'У вас 4 задачи: 2 выполнены, 1 в работе, 1 ожидает. Хотите подробности по какой-то?'),
                ('user', 'Покажи партнеров'),
                ('agent', 'Нашел 3 потенциальных партнера в вашем городе с похожими интересами. Проверьте раздел "Партнеры"!')
            ]
            for msg_type, content in interactions_data:
                interaction = Interaction(
                    user_id=demo_user.id,
                    message_type=msg_type,
                    content=content,
                    created_at=datetime.now(pytz.UTC) - timedelta(minutes=len(interactions_data) - interactions_data.index((msg_type, content)))
                )
                session.add(interaction)
            session.commit()
            
    except Exception as e:
        logger.error(f"Error creating demo data: {e}")
    finally:
        session.close()


# Global app for Railway
app = web.Application()
aiohttp_jinja2.setup(app, loader=jinja2.FileSystemLoader('templates'))
aiohttp_session.setup(app, SimpleCookieStorage())

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

bot = Bot(token=TELEGRAM_TOKEN)

# Routes
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

# Setup for production
dp = Dispatcher()
dp.include_router(router)

webhook_requests_handler = SimpleRequestHandler(
    dispatcher=dp,
    bot=bot,
)
webhook_requests_handler.register(app, path="/webhook")

setup_application(app, dp, bot=bot)

# Add startup handler
app.on_startup.append(on_startup)


async def main():
    global app
    logger.info("Starting main function")
    logger.info(f"LOCAL env: {repr(os.getenv('LOCAL'))}")
    # Создание таблиц
    Base.metadata.create_all(engine)
    logger.info("Database tables created")

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
        
        app.on_startup.append(on_startup)
        
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
    if os.getenv("LOCAL") == "1":
        logger.info("Running main in local mode")
        try:
            asyncio.run(main())
        except Exception as e:
            logger.error(f"Error in main: {e}")
            import traceback
            traceback.print_exc()
    else:
        logger.info("Running app in production mode")
        port = int(os.getenv("PORT"))
        logger.info(f"Starting web app on port {port}")
        web.run_app(app, port=port, host='0.0.0.0')
