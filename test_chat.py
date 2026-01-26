import asyncio
import aiohttp
from aiohttp import web
import os
os.environ['LOCAL'] = '1'
from config import PORT

async def chat_handler(request):
    data = await request.post()
    message = data.get('message', 'No message')
    print(f'Received message: {message}')
    return web.json_response({'response': f'Chat response to: {message}'})

app = web.Application()
app.router.add_post('/chat', chat_handler)

async def test_chat():
    # Start server
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    print(f'Server started on 0.0.0.0:{PORT}')

    # Wait a bit
    await asyncio.sleep(1)

    # Send test request
    async with aiohttp.ClientSession() as session:
        data = {'message': 'Hello AI'}
        async with session.post('http://localhost:8080/chat', data=data) as response:
            result = await response.json()
            print(f'Response: {result}')

    # Keep server running for a bit
    await asyncio.sleep(2)

    await runner.cleanup()

if __name__ == '__main__':
    asyncio.run(test_chat())