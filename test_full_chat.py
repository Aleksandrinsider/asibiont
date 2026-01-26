import asyncio
import aiohttp
from aiohttp import web
import logging
import sys
from ai_integration.chat import chat_with_ai
from models import Session
from config import PORT

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

async def chat_handler(request):
    """Handle chat messages"""
    try:
        logger.info("Received POST request to /chat")
        data = await request.post()
        message = data.get('message', '')
        logger.info(f"Message received: '{message}'")

        if not message:
            logger.warning("No message provided in request")
            return web.json_response({'error': 'No message provided'}, status=400)

        logger.info(f"Processing chat message: {message}")

        # Get AI response
        db_session = Session()
        try:
            ai_response = await chat_with_ai(message, user_id=1, db_session=db_session)
            logger.info(f"AI response generated: {ai_response[:100]}...")
            return web.json_response({'response': ai_response})
        except Exception as e:
            logger.error(f"Error in chat_with_ai: {e}", exc_info=True)
            return web.json_response({'error': str(e)}, status=500)
        finally:
            db_session.close()

    except Exception as e:
        logger.error(f"Error in chat handler: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)

async def create_app():
    app = web.Application()
    app.router.add_post('/chat', chat_handler)
    return app

async def test_full_chat():
    """Test the full chat functionality"""
    app = await create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"Server started on 0.0.0.0:{PORT}")

    # Give server time to start
    await asyncio.sleep(1)

    # Send test request
    try:
        async with aiohttp.ClientSession() as session:
            data = {'message': 'Hello AI'}
            async with session.post(f'http://localhost:{PORT}/chat', data=data) as response:
                result = await response.json()
                print(f"Chat test successful! Response: {result.get('response', '')[:300]}")
                print("SUCCESS: AI chat is working!")
    except Exception as e:
        print(f"Chat test failed: {e}")

    # Stop server
    await runner.cleanup()
    logger.info("Server stopped")

if __name__ == "__main__":
    asyncio.run(test_full_chat())