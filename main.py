import asyncio
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from config import TELEGRAM_TOKEN, WEBHOOK_URL
from handlers import router
from reminder_service import ReminderService
from ai_integration import AIIntegration
from models import Base, engine, Session, Subscription, User, Task, UserProfile, Interaction
import os
import datetime
import pytz
from datetime import timedelta


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
        await bot.delete_webhook()
        print("Webhook deleted for local mode")

        # Setup web server
        app = web.Application()
        app.router.add_get('/issues', issues_handler)
        app.router.add_get('/dashboard', dashboard_handler)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', 8000)
        await site.start()
        print("Web server started on port 8000")

        # Start polling in background
        polling_task = asyncio.create_task(dp.start_polling(bot))
        print("Polling started")

        # Keep running
        try:
            await asyncio.sleep(float('inf'))
        except KeyboardInterrupt:
            print("Shutting down...")
        finally:
            await runner.cleanup()
            polling_task.cancel()
    else:
        # Вебхук для Railway
        print("Setting up webhook for Railway")
        app = web.Application()
        webhook_requests_handler = SimpleRequestHandler(
            dispatcher=dp,
            bot=bot,
        )
        webhook_requests_handler.register(app, path="/webhook")

        setup_application(app, dp, bot=bot)

        app.router.add_post('/yookassa-webhook', yookassa_webhook)

        # Add dashboard route
        app.router.add_get('/dashboard', dashboard_handler)
        app.router.add_get('/issues', issues_handler)

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


async def dashboard_handler(request):
    telegram_id = request.query.get('telegram_id')
    if not telegram_id:
        return web.Response(text="Telegram ID required", status=400)

    session = Session()
    user = session.query(User).filter_by(telegram_id=int(telegram_id)).first()
    if not user:
        session.close()
        return web.Response(text="User not found", status=404)

    # Get user metrics
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    tasks = session.query(Task).filter_by(user_id=user.id).all()
    interactions = session.query(Interaction).filter_by(user_id=user.id).all()
    subscription = session.query(Subscription).filter_by(user_id=user.id).first()

    total_tasks = len(tasks)
    completed_tasks = len([t for t in tasks if t.status == 'completed'])
    pending_tasks = len([t for t in tasks if t.status == 'pending'])
    skipped_tasks = len([t for t in tasks if t.status == 'skipped'])

    # Calculate average completion time if available
    avg_completion_time = profile.average_completion_time if profile else 0

    # Recent interactions
    recent_interactions = sorted(interactions, key=lambda x: x.created_at, reverse=True)[:10]

    session.close()

    # Generate HTML
    html = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Панель управления задачами</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; background-color: #f4f4f4; }}
            .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }}
            .header {{ text-align: center; margin-bottom: 30px; }}
            .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }}
            .metric {{ background: #f8f9fa; padding: 20px; border-radius: 8px; text-align: center; }}
            .metric h3 {{ margin: 0; color: #333; }}
            .metric p {{ margin: 10px 0 0 0; font-size: 24px; font-weight: bold; color: #007bff; }}
            .interactions {{ margin-top: 30px; }}
            .interaction {{ border-bottom: 1px solid #eee; padding: 10px 0; }}
            .interaction .type {{ font-weight: bold; color: #28a745; }}
            .interaction .content {{ margin-top: 5px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Панель управления задачами</h1>
                <p>Пользователь: {user.first_name or user.username}</p>
                <p>Статус подписки: {subscription.status if subscription else 'Нет'}</p>
            </div>

            <div class="metrics">
                <div class="metric">
                    <h3>Всего задач</h3>
                    <p>{total_tasks}</p>
                </div>
                <div class="metric">
                    <h3>Завершено</h3>
                    <p>{completed_tasks}</p>
                </div>
                <div class="metric">
                    <h3>В ожидании</h3>
                    <p>{pending_tasks}</p>
                </div>
                <div class="metric">
                    <h3>Пропущено</h3>
                    <p>{skipped_tasks}</p>
                </div>
                <div class="metric">
                    <h3>Среднее время завершения</h3>
                    <p>{avg_completion_time} мин</p>
                </div>
            </div>

            <div class="interactions">
                <h2>Последние взаимодействия</h2>
                {"".join([f'<div class="interaction"><div class="type">{i.message_type}</div>'
                         f'<div class="content">{i.content[:100]}...</div></div>' for i in recent_interactions])}
            </div>
        </div>
    </body>
    </html>
    """

    return web.Response(text=html, content_type='text/html')


async def issues_handler(request):
    telegram_id = request.query.get('telegram_id')
    if not telegram_id:
        return web.Response(text="Telegram ID required", status=400)

    filter_status = request.query.get('q', 'is:open')  # Default to open issues

    session = Session()
    user = session.query(User).filter_by(telegram_id=int(telegram_id)).first()
    if not user:
        session.close()
        return web.Response(text="User not found", status=404)

    query = session.query(Task).filter_by(user_id=user.id)
    if 'is:open' in filter_status:
        query = query.filter(Task.status == 'pending')
    elif 'is:closed' in filter_status:
        query = query.filter(Task.status == 'completed')
    tasks = query.order_by(Task.created_at.desc()).all()
    session.close()

    # Count open and closed
    open_count = len([t for t in tasks if t.status == 'pending'])
    closed_count = len([t for t in tasks if t.status == 'completed'])

    # Generate GitHub-like HTML for issues
    issues_html = ""
    for task in tasks:
        status = "Open" if task.status == 'pending' else "Closed"
        status_class = "open" if task.status == 'pending' else "closed"
        priority_label = task.priority.capitalize()
        priority_class = {"High": "high", "Medium": "medium", "Low": "low"}.get(priority_label, "medium")
        due_date_str = task.due_date.strftime("%b %d, %Y") if task.due_date else "No due date"
        created_str = task.created_at.strftime("%b %d, %Y")

        issues_html += f"""
        <div class="issue">
            <div class="issue-header">
                <span class="status {status_class}">{status}</span>
                <a href="#" class="issue-title">{task.title}</a>
                <span class="priority {priority_class}">{priority_label}</span>
            </div>
            <div class="issue-meta">
                <span>#{task.id}</span>
                <span>opened on {created_str}</span>
                {f'<span>due on {due_date_str}</span>' if task.due_date else ''}
            </div>
            <div class="issue-description">{task.description or 'No description'}</div>
        </div>
        """

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Issues · {user.first_name or user.username}</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
                background-color: #0d1117;
                color: #c9d1d9;
                margin: 0;
                padding: 0;
            }}
            .header {{
                background-color: #161b22;
                border-bottom: 1px solid #30363d;
                padding: 16px 32px;
                display: flex;
                align-items: center;
            }}
            .header h1 {{
                margin: 0;
                font-size: 24px;
                color: #f0f6fc;
            }}
            .header .repo-info {{
                margin-left: 8px;
                color: #8b949e;
            }}
            .nav {{
                background-color: #161b22;
                border-bottom: 1px solid #30363d;
                padding: 0 32px;
            }}
            .nav-tabs {{
                display: flex;
                list-style: none;
                margin: 0;
                padding: 0;
            }}
            .nav-tabs li {{
                margin-right: 24px;
            }}
            .nav-tabs a {{
                color: #c9d1d9;
                text-decoration: none;
                padding: 16px 8px;
                display: block;
                border-bottom: 2px solid transparent;
            }}
            .nav-tabs a.active {{
                color: #f0f6fc;
                border-bottom-color: #fd7e14;
            }}
            .filters {{
                padding: 16px 32px;
                background-color: #161b22;
                border-bottom: 1px solid #30363d;
            }}
            .filters a {{
                color: #58a6ff;
                text-decoration: none;
                margin-right: 16px;
            }}
            .issues {{
                padding: 24px 32px;
            }}
            .issue {{
                border: 1px solid #30363d;
                border-radius: 6px;
                background-color: #161b22;
                padding: 16px;
                margin-bottom: 16px;
            }}
            .issue-header {{
                display: flex;
                align-items: center;
                margin-bottom: 8px;
            }}
            .status {{
                padding: 4px 8px;
                border-radius: 12px;
                font-size: 12px;
                font-weight: bold;
                margin-right: 8px;
            }}
            .status.open {{
                background-color: #238636;
                color: #ffffff;
            }}
            .status.closed {{
                background-color: #da3633;
                color: #ffffff;
            }}
            .issue-title {{
                color: #58a6ff;
                text-decoration: none;
                font-weight: 600;
                margin-right: 8px;
            }}
            .priority {{
                padding: 2px 6px;
                border-radius: 12px;
                font-size: 12px;
                font-weight: bold;
            }}
            .priority.high {{
                background-color: #da3633;
                color: #ffffff;
            }}
            .priority.medium {{
                background-color: #d29922;
                color: #ffffff;
            }}
            .priority.low {{
                background-color: #238636;
                color: #ffffff;
            }}
            .issue-meta {{
                color: #8b949e;
                font-size: 14px;
                margin-bottom: 8px;
            }}
            .issue-meta span {{
                margin-right: 16px;
            }}
            .issue-description {{
                color: #c9d1d9;
                font-size: 14px;
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>{user.first_name or user.username}</h1>
            <span class="repo-info">/ tasks</span>
        </div>
        <nav class="nav">
            <ul class="nav-tabs">
                <li><a href="#" class="active">Issues</a></li>
                <li><a href="#">Pull requests</a></li>
            </ul>
        </nav>
        <div class="filters">
            <a href="?telegram_id={telegram_id}&q=is:open" class="{'active' if 'is:open' in filter_status else ''}">Open ({open_count})</a>
            <a href="?telegram_id={telegram_id}&q=is:closed" class="{'active' if 'is:closed' in filter_status else ''}">Closed ({closed_count})</a>
        </div>
        <div class="issues">
            {issues_html}
        </div>
    </body>
    </html>
    """

    return web.Response(text=html, content_type='text/html')


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
