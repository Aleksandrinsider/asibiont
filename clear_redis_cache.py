"""Clear Redis cache for user to force reload from database"""
import asyncio
import redis.asyncio as aioredis

REDIS_URL = "redis://default:LnTts6f2dnlRVOf1tvwahzXZcun60kO8@redis-18169.c300.eu-central-1-1.ec2.redns.redis-cloud.com:18169"

async def main():
    redis_client = await aioredis.from_url(REDIS_URL, decode_responses=False)
    
    # Clear context cache for user
    user_id = 146333757
    await redis_client.delete(f"context:{user_id}")
    print(f"✅ Cleared Redis context cache for user {user_id}")
    
    await redis_client.close()

if __name__ == "__main__":
    asyncio.run(main())
