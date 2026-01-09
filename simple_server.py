import asyncio
import os
from aiohttp import web
from dotenv import load_dotenv

# Force local mode
os.environ['LOCAL'] = '1'
os.environ['FREE_ACCESS_MODE'] = 'true'

load_dotenv()

async def health_handler(request):
    return web.json_response({'status': 'ok', 'message': 'Server is running'})

async def login_handler(request):
    return web.json_response({'message': 'Login page'})

async def create_app():
    app = web.Application()

    # Add routes
    app.router.add_get('/health', health_handler)
    app.router.add_get('/login', login_handler)

    return app

async def run_test_server():
    app = await create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', 3000)
    await site.start()
    print("Test server started on http://localhost:8000")
    print("Press Ctrl+C to stop")

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("Stopping server...")
    finally:
        await runner.cleanup()

if __name__ == "__main__":
    asyncio.run(run_test_server())