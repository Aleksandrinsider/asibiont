import asyncio
import redis.asyncio as aioredis
from config import REDIS_URL

async def check():
    r = await aioredis.from_url(REDIS_URL, encoding='utf-8', decode_responses=True)
    key = 'context:146333757'
    val = await r.get(key)
    print(f'Redis key "{key}": {val}')
    await r.aclose()

asyncio.run(check())
