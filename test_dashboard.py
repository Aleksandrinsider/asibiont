from aiohttp import web
from models import Base, engine, Session, User, Task, UserProfile, Interaction, Subscription

Base.metadata.create_all(engine)

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
                {"".join([f'<div class="interaction"><div class="type">{i.message_type}</div><div class="content">{i.content[:100]}...</div></div>' for i in recent_interactions])}
            </div>
        </div>
    </body>
    </html>
    """
    
    return web.Response(text=html, content_type='text/html')

async def main():
    app = web.Application()
    app.router.add_get('/dashboard', dashboard_handler)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', 8000)
    await site.start()
    print("Dashboard server started on http://localhost:8000/dashboard?telegram_id=12345")
    
    # Test the endpoint
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.get('http://localhost:8000/dashboard?telegram_id=12345') as resp:
            html = await resp.text()
            print("Response status:", resp.status)
            print("HTML length:", len(html))
            print("First 500 chars:", html[:500])
    
    await runner.cleanup()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())