import asyncio
import aiohttp
from aiohttp import web
import os
os.environ['LOCAL'] = '1'

async def chat_handler(request):
    data = await request.post()
    message = data.get('message', 'No message')
    print(f'[CHAT] Received: {message}')
    return web.json_response({'response': f'AI: {message}'})

async def dashboard_handler(request):
    html = '''
    <html><body>
    <h1>Test Dashboard</h1>
    <form id="form">
        <input name="message" id="msg" placeholder="Type message">
        <button type="button" onclick="send()">Send</button>
    </form>
    <div id="response"></div>
    <script>
    async function send() {
        const form = new FormData(document.getElementById('form'));
        const response = await fetch('/chat', {method: 'POST', body: form});
        const data = await response.json();
        document.getElementById('response').innerHTML = data.response;
    }
    </script>
    </body></html>
    '''
    return web.Response(text=html, content_type='text/html')

app = web.Application()
app.router.add_post('/chat', chat_handler)
app.router.add_get('/dashboard', dashboard_handler)

async def test_full_flow():
    # Start server
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    print('Server started on 0.0.0.0:8080')

    # Wait for server to be ready
    await asyncio.sleep(1)

    # Test chat endpoint directly
    print('Testing chat endpoint...')
    async with aiohttp.ClientSession() as session:
        data = {'message': 'Hello AI'}
        async with session.post('http://localhost:8080/chat', data=data) as response:
            result = await response.json()
            print(f'Chat response: {result}')

    print('Test completed successfully!')
    await runner.cleanup()

if __name__ == '__main__':
    asyncio.run(test_full_flow())