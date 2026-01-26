import asyncio
import aiohttp
import os
os.environ['LOCAL'] = '1'

async def test_chat_flow():
    await asyncio.sleep(3)  # Wait for server to start

    jar = aiohttp.CookieJar()
    async with aiohttp.ClientSession(cookie_jar=jar) as session:
        # Get dashboard to establish session
        print("Getting dashboard...")
        async with session.get('http://localhost:8080/dashboard') as response:
            print(f"Dashboard status: {response.status}")
            html = await response.text()
            print(f"Dashboard received: {len(html)} chars")

        # Send chat message
        print("Sending chat message...")
        data = {'message': 'Hello AI, this is a test'}
        async with session.post('http://localhost:8080/chat', data=data) as response:
            print(f"Chat response status: {response.status}")
            if response.status == 200:
                result = await response.json()
                print(f"Chat response: {result}")
            else:
                text = await response.text()
                print(f"Chat error: {text}")

if __name__ == '__main__':
    asyncio.run(test_chat_flow())