from config import LOCAL

# Conditional setup based on environment
if LOCAL:
    # Mock router for local development
    class MockRouter:
        pass
    router = MockRouter()
    
    async def init_redis(redis_client):
        pass
else:
    from aiogram import Router
    router = Router()
    
    async def init_redis(redis_client):
        pass