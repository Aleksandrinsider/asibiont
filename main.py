import asyncio
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from config import TELEGRAM_TOKEN, WEBHOOK_URL
from handlers import router
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timezone

bot = Bot(token=TELEGRAM_TOKEN)

async def send_reminder(task_id, user_id):
    session = Session()
    task = session.query(Task).filter_by(id=task_id).first()
    session.close()
    if task and task.status == 'pending':
        await bot.send_message(user_id, f"Напоминание: {task.title}")

async def schedule_reminders(scheduler):
    session = Session()
    tasks = session.query(Task).filter(Task.reminder_time.isnot(None), Task.status == 'pending').all()
    session.close()
    for task in tasks:
        if task.reminder_time > datetime.now(timezone.utc):
            scheduler.add_job(send_reminder, 'date', run_date=task.reminder_time, args=[task.id, task.user_id])

async def on_startup(bot: Bot):
    await bot.set_webhook(WEBHOOK_URL)
    # Запустить scheduler
    scheduler = AsyncIOScheduler()
    await schedule_reminders(scheduler)
    scheduler.start()

async def main():
    dp = Dispatcher()
    dp.include_router(router)

    # Проверка на локальный запуск
    if os.getenv("LOCAL") == "1":
        # Локальный запуск с polling
        print("Запуск в локальном режиме (polling)...")
        await dp.start_polling(bot)
    else:
        # Вебхук для Railway
        app = web.Application()
        webhook_requests_handler = SimpleRequestHandler(
            dispatcher=dp,
            bot=bot,
        )
        webhook_requests_handler.register(app, path="/webhook")

        setup_application(app, dp, bot=bot)

        await on_startup(bot)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', 8080)
        await site.start()

        print("Бот запущен в режиме вебхуков!")

if __name__ == "__main__":
    asyncio.run(main())