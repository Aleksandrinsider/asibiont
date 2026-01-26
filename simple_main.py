import asyncio
from aiohttp import web
import logging
import os
from ai_integration.chat import chat_with_ai
from models import Session
from config import PORT

logging.basicConfig(level=logging.INFO)
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

async def health_handler(request):
    return web.json_response({'status': 'ok'})

async def dashboard_handler(request):
    return web.Response(text="Dashboard placeholder")

async def create_app():
    app = web.Application()

    # Add routes
    app.router.add_post('/chat', chat_handler)
    app.router.add_get('/health', health_handler)
    app.router.add_get('/dashboard', dashboard_handler)

    return app

async def run_server():
    app = await create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"Server started on 0.0.0.0:{PORT}")
    logger.info(f"Health check endpoint: http://0.0.0.0:{PORT}/health")
    logger.info(f"Dashboard endpoint: http://0.0.0.0:{PORT}/dashboard")
    logger.info("Server is ready to accept connections")

    # Keep the server running
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        logger.info("Shutting down server...")
    finally:
        await runner.cleanup()
        logger.info("Server shut down")

if __name__ == "__main__":
    asyncio.run(run_server())