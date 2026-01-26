import asyncio
from aiohttp import web
import os
os.environ['LOCAL'] = '1'
from config import PORT

async def chat_handler(request):
    data = await request.post()
    message = data.get('message', 'No message')
    return web.json_response({'response': f'Chat response to: {message}'})

app = web.Application()
app.router.add_post('/chat', chat_handler)

async def run_server():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    print(f'Server started on 0.0.0.0:{PORT}')
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print('Shutting down...')
    finally:
        await runner.cleanup()

if __name__ == '__main__':
    asyncio.run(run_server())