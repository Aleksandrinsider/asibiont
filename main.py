import asyncio
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from config import TELEGRAM_TOKEN, WEBHOOK_URL
from handlers import router
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timezone
import os
from models import Session, Task

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
                task.reminder_time = task.reminder_time.replace(tzinfo=timezone.utc)
            if task.reminder_time > datetime.now(timezone.utc):
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
    # Запустить scheduler
    print("Starting scheduler")
    scheduler = AsyncIOScheduler()
    await schedule_reminders(scheduler)
    scheduler.start()
    print("Scheduler started")

async def main():
    print("Starting main function")
    dp = Dispatcher()
    dp.include_router(router)
    print("Dispatcher created and router included")

    # Проверка на локальный запуск
    if os.getenv("LOCAL") == "1":
        # Локальный запуск с polling
        print("Запуск в локальном режиме (polling)...")
        await dp.start_polling(bot)
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

        print("Calling on_startup")
        await on_startup(bot)

        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.getenv("PORT", 8080))
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()

        print(f"Бот запущен в режиме вебхуков на порту {port}!")

        # Keep the event loop running
        try:
            await asyncio.Future()  # run forever
        except KeyboardInterrupt:
            print("Shutting down...")
        finally:
            await runner.cleanup()

if __name__ == "__main__":
    print("Running main")
    asyncio.run(main())