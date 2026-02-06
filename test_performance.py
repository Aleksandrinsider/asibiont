import asyncio
import time
from ai_integration.chat import chat_with_ai

async def measure_performance():
    """Измеряет производительность агента"""
    
    print("=== PERFORMANCE TEST ===\n")
    
    user_id = 123456789
    
    test_cases = [
        ("привет", "Simple greeting"),
        ("покажи мои задачи", "List tasks"),
        ("создай задачу позвонить завтра в 10:00", "Add task with time parsing"),
        ("проанализируй задачи", "Analytics"),
    ]
    
    total_time = 0
    results = []
    
    for message, description in test_cases:
        print(f"\n[TEST] {description}")
        print(f"[MSG] {message}")
        print("-" * 60)
        
        start = time.time()
        
        try:
            result = await chat_with_ai(message, user_id=user_id)
            
            elapsed = time.time() - start
            total_time += elapsed
            
            response = result.get('response', '')
            tool_calls = result.get('tool_calls', [])
            
            print(f"[TIME] {elapsed:.2f}s")
            print(f"[TOOLS] {len(tool_calls)} commands")
            print(f"[RESPONSE] {response[:100]}...")
            
            results.append({
                'description': description,
                'time': elapsed,
                'tools': len(tool_calls)
            })
            
        except Exception as e:
            print(f"[ERROR] {e}")
            results.append({
                'description': description,
                'time': -1,
                'tools': 0
            })
    
    # Итоги
    print("\n" + "="*60)
    print("PERFORMANCE SUMMARY")
    print("="*60)
    
    for r in results:
        if r['time'] > 0:
            print(f"{r['description']:30} | {r['time']:5.2f}s | {r['tools']} tools")
    
    avg_time = total_time / len([r for r in results if r['time'] > 0])
    
    print("-" * 60)
    print(f"Average response time: {avg_time:.2f}s")
    print(f"Total time: {total_time:.2f}s")
    
    # Прогноз масштабируемости
    print("\n" + "="*60)
    print("SCALABILITY ANALYSIS")
    print("="*60)
    
    # Предполагаем что каждый пользователь делает 1 запрос в минуту
    requests_per_minute = 1000  # 1000 пользователей
    parallel_capacity = 60 / avg_time  # сколько запросов можем обработать за минуту последовательно
    
    print(f"Concurrent users: 1000")
    print(f"Requests per minute: {requests_per_minute}")
    print(f"Sequential capacity: {parallel_capacity:.0f} req/min")
    print(f"Parallel workers needed: {requests_per_minute / parallel_capacity:.0f}x")
    
    if parallel_capacity < requests_per_minute:
        print(f"\n[WARNING] Need async optimization!")
        print(f"Current: {parallel_capacity:.0f} req/min")
        print(f"Required: {requests_per_minute} req/min")
        print(f"Gap: {requests_per_minute - parallel_capacity:.0f} req/min")
    else:
        print(f"\n[OK] Can handle 1000 users with current performance")

async def test_concurrent_load():
    """Тест одновременной нагрузки"""
    
    print("\n\n" + "="*60)
    print("CONCURRENT LOAD TEST")
    print("="*60)
    print("[*] Simulating 10 concurrent users...")
    
    user_ids = list(range(100, 110))
    messages = [
        "привет",
        "покажи задачи",
        "создай задачу тест",
        "проанализируй задачи",
        "как дела",
    ]
    
    start = time.time()
    
    tasks = []
    for i, user_id in enumerate(user_ids):
        message = messages[i % len(messages)]
        task = chat_with_ai(message, user_id=user_id)
        tasks.append(task)
    
    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        elapsed = time.time() - start
        
        successful = len([r for r in results if not isinstance(r, Exception)])
        failed = len([r for r in results if isinstance(r, Exception)])
        
        print(f"\n[TIME] {elapsed:.2f}s for 10 concurrent requests")
        print(f"[OK] Successful: {successful}/10")
        print(f"[FAIL] Failed: {failed}/10")
        print(f"[AVG] {elapsed/10:.2f}s per request (parallel)")
        
        # Extrapolate to 1000 users
        time_for_1000 = (elapsed / 10) * 1000
        print(f"\n[ESTIMATE] 1000 concurrent users would take ~{time_for_1000:.0f}s")
        print(f"[ESTIMATE] With 10 workers: ~{time_for_1000/10:.0f}s")
        print(f"[ESTIMATE] With 100 workers: ~{time_for_1000/100:.0f}s")
        
    except Exception as e:
        print(f"[ERROR] Concurrent test failed: {e}")

if __name__ == "__main__":
    asyncio.run(measure_performance())
    asyncio.run(test_concurrent_load())
