import asyncio
import time
from ai_integration.chat import chat_with_ai

async def test_cache():
    """Тест кэширования time parser"""
    
    print("=== TIME PARSER CACHE TEST ===\n")
    
    user_id = 123456789
    
    # Первый вызов - без кэша
    print("[TEST 1] First call - no cache")
    start = time.time()
    result1 = await chat_with_ai("создай задачу тест завтра в 10:00", user_id=user_id)
    time1 = time.time() - start
    print(f"[TIME] {time1:.2f}s\n")
    
    # Второй вызов - с кэшем
    print("[TEST 2] Second call - should use cache")
    start = time.time()
    result2 = await chat_with_ai("создай задачу тест2 завтра в 10:00", user_id=user_id)
    time2 = time.time() - start
    print(f"[TIME] {time2:.2f}s\n")
    
    # Третий вызов - другое время
    print("[TEST 3] Different time - no cache")
    start = time.time()
    result3 = await chat_with_ai("создай задачу тест3 послезавтра в 15:00", user_id=user_id)
    time3 = time.time() - start
    print(f"[TIME] {time3:.2f}s\n")
    
    # Четвертый вызов - повтор второго времени
    print("[TEST 4] Repeat time from test 2 - should use cache")
    start = time.time()
    result4 = await chat_with_ai("создай задачу тест4 завтра в 10:00", user_id=user_id)
    time4 = time.time() - start
    print(f"[TIME] {time4:.2f}s\n")
    
    print("="*60)
    print("CACHE EFFECTIVENESS")
    print("="*60)
    print(f"Test 1 (no cache):    {time1:.2f}s")
    print(f"Test 2 (with cache):  {time2:.2f}s - {((time1-time2)/time1*100):.0f}% faster")
    print(f"Test 3 (no cache):    {time3:.2f}s")
    print(f"Test 4 (with cache):  {time4:.2f}s - {((time3-time4)/time3*100):.0f}% faster")
    
    if time2 < time1 * 0.9:
        print("\n[OK] Cache is working!")
    else:
        print("\n[WARN] Cache not working as expected")

if __name__ == "__main__":
    asyncio.run(test_cache())
