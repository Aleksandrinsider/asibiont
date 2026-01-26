import asyncio
from aiohttp import web
import os
os.environ['LOCAL'] = '1'

# Simple chat handler for testing
async def chat_handler(request):
    data = await request.post()
    message = data.get('message', 'No message')
    print(f'[CHAT] Received message: {message}')
    return web.json_response({'response': f'AI Response to: {message}'})

# Simple dashboard handler
async def dashboard_handler(request):
    return web.Response(text='<html><body><h1>Dashboard</h1><div id="chat"><input id="msg" type="text"><button onclick="send()">Send</button></div><script>function send(){fetch("/chat",{method:"POST",body:new FormData(document.getElementById("form"))}).then(r=>r.json()).then(d=>alert(d.response))}</script></body></html>', content_type='text/html')

app = web.Application()
app.router.add_post('/chat', chat_handler)
app.router.add_get('/dashboard', dashboard_handler)

async def run_server():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    print('Server started on 0.0.0.0:8080')
    print('Dashboard: http://localhost:8080/dashboard')
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print('Shutting down...')
    finally:
        await runner.cleanup()

if __name__ == '__main__':
    asyncio.run(run_server())