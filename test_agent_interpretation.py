"""
Тест для проверки правильности интерпретации агентом различных сообщений
"""
import asyncio
import os
os.environ['LOCAL'] = '1'

from ai_integration.chat import chat_with_ai
from ai_integration.utils import parse_multiple_tasks, post_process_tool_calls
from models import User, Task
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Test database
engine = create_engine('sqlite:///test_agent.db', echo=False)
Session = sessionmaker(bind=engine)

TEST_USER_ID = 999999999

# Test cases with expected behavior
TEST_CASES = [
    # 1. Arrival messages - should NOT trigger completion
    {
        "message": "я только с пробежки",
        "expected_intent": "conversation",
        "should_not_complete": True,
        "description": "Arrival from jogging should not complete task"
    },
    {
        "message": "вернулся с тренировки",
        "expected_intent": "conversation", 
        "should_not_complete": True,
        "description": "Return from workout should not complete task"
    },
    {
        "message": "только с работы пришел",
        "expected_intent": "conversation",
        "should_not_complete": True,
        "description": "Arrival from work should not complete task"
    },
    
    # 2. Actual completion messages - SHOULD trigger completion
    {
        "message": "сделал пробежку",
        "expected_intent": "complete_task",
        "should_complete": True,
        "description": "Completion statement should trigger completion"
    },
    {
        "message": "выполнил тренировку",
        "expected_intent": "complete_task",
        "should_complete": True,
        "description": "Task completion should be detected"
    },
    {
        "message": "готово, позвонил маме",
        "expected_intent": "complete_task",
        "should_complete": True,
        "description": "Done with specific task should complete"
    },
    
    # 3. Multiple task creation
    {
        "message": "создай задачу разбудить сына и погулять с собакой",
        "expected_intent": "add_task",
        "expected_tasks": 2,
        "expected_titles": ["разбудить сына", "погулять с собакой"],
        "description": "Should create 2 separate tasks"
    },
    {
        "message": "добавь позвонить маме, купить продукты и сходить в банк",
        "expected_intent": "add_task",
        "expected_tasks": 3,
        "expected_titles": ["позвонить маме", "купить продукты", "сходить в банк"],
        "description": "Should create 3 tasks from comma-separated list"
    },
    
    # 4. Bad task titles - should NOT create tasks
    {
        "message": "да создай задачу с таким заголовком",
        "expected_intent": "add_task",
        "should_reject": True,
        "description": "Should reject task with command words in title"
    },
    {
        "message": "создай задачу ок",
        "expected_intent": "add_task",
        "should_reject": True,
        "description": "Should reject too short task title"
    },
    {
        "message": "добавь задачу",
        "expected_intent": "add_task",
        "should_reject": True,
        "description": "Should reject empty task without title"
    },
    
    # 5. Conversation - should NOT create tasks
    {
        "message": "как дела?",
        "expected_intent": "conversation",
        "should_not_create_task": True,
        "description": "Greeting should not create task"
    },
    {
        "message": "расскажи о себе",
        "expected_intent": "conversation",
        "should_not_create_task": True,
        "description": "Question should not create task"
    },
    
    # 6. Emotional messages - should NOT auto-list tasks
    {
        "message": "я устал",
        "expected_intent": "conversation",
        "should_not_list_tasks": True,
        "description": "Emotion should not auto-list tasks"
    },
    {
        "message": "не знаю что делать",
        "expected_intent": "conversation",
        "should_not_list_tasks": True,
        "description": "Uncertainty should not auto-list tasks"
    },
    
    # 7. Task listing requests - SHOULD list
    {
        "message": "покажи мои задачи",
        "expected_intent": "list_tasks",
        "should_list": True,
        "description": "Direct request should list tasks"
    },
    {
        "message": "что у меня запланировано?",
        "expected_intent": "list_tasks",
        "should_list": True,
        "description": "Question about schedule should list"
    },
]


def test_parse_multiple_tasks():
    """Test task parsing logic"""
    print("\n=== TESTING parse_multiple_tasks ===")
    
    test_cases = [
        ("создай задачу разбудить сына и погулять с собакой", 2),
        ("добавь позвонить маме, купить продукты и сходить в банк", 3),
        ("напомни позвонить маме", 1),
        ("да создай задачу с таким заголовком", 1),  # Will be filtered later
        ("создай задачу ок", 1),  # Will be filtered later
    ]
    
    failures = []
    for message, expected_count in test_cases:
        tasks = parse_multiple_tasks(message)
        if len(tasks) != expected_count:
            failures.append(f"❌ '{message}' -> got {len(tasks)} tasks, expected {expected_count}")
            print(f"❌ '{message}'")
            print(f"   Expected: {expected_count} tasks")
            print(f"   Got: {len(tasks)} tasks: {tasks}")
        else:
            print(f"✅ '{message}' -> {len(tasks)} tasks")
            for task in tasks:
                print(f"   - '{task['title']}'")
    
    return failures


def test_quality_filters():
    """Test quality filtering in post_process_tool_calls"""
    print("\n=== TESTING QUALITY FILTERS ===")
    
    test_cases = [
        ("да создай задачу с таким заголовком", True),  # Should be rejected
        ("создай задачу ок", True),  # Should be rejected (too short)
        ("создай задачу разбудить сына", False),  # Should pass
        ("добавь позвонить маме", False),  # Should pass
        ("напомни купить продукты", False),  # Should pass
    ]
    
    failures = []
    for message, should_reject in test_cases:
        tasks = parse_multiple_tasks(message)
        
        # Simulate quality filter
        valid_tasks = []
        for task_info in tasks:
            title = task_info["title"].strip()
            is_bad = (
                len(title) < 3 or
                any(word in title.lower() for word in ['создай', 'задачу', 'задач', 'добавь', 'напомни', 'сделай', 'таким', 'этим']) or
                title.lower() in ['да', 'нет', 'ок', 'хорошо', 'ладно']
            )
            if not is_bad:
                valid_tasks.append(task_info)
        
        will_reject = len(valid_tasks) == 0
        
        if will_reject != should_reject:
            failures.append(f"❌ '{message}' -> reject={will_reject}, expected={should_reject}")
            print(f"❌ '{message}'")
            print(f"   Title: '{tasks[0]['title'] if tasks else 'N/A'}'")
            print(f"   Expected to reject: {should_reject}")
            print(f"   Actually rejected: {will_reject}")
        else:
            status = "rejected ❌" if will_reject else "accepted ✅"
            print(f"✅ '{message}' -> {status}")
            if not will_reject and tasks:
                print(f"   Title: '{tasks[0]['title']}'")
    
    return failures


def test_completion_patterns():
    """Test that arrival messages don't trigger completion"""
    print("\n=== TESTING COMPLETION PATTERNS ===")
    
    # Read completion patterns from chat.py
    import re
    with open('ai_integration/chat.py', 'r', encoding='utf-8') as f:
        content = f.read()
        
    # Find completion_patterns list
    match = re.search(r'completion_patterns = \[(.*?)\]', content, re.DOTALL)
    if match:
        patterns_text = match.group(1)
        patterns = re.findall(r"r'([^']+)'", patterns_text)
        
        print(f"Found {len(patterns)} completion patterns:")
        for p in patterns[:5]:
            print(f"  - {p}")
        print("  ...")
    
    # Test arrival messages
    arrival_messages = [
        "я только с пробежки",
        "вернулся с тренировки",
        "только с работы пришел",
        "пришел из магазина",
        "только домой зашел",
    ]
    
    print("\nChecking arrival messages don't match completion patterns:")
    failures = []
    for msg in arrival_messages:
        if match:
            matched_any = False
            for pattern in patterns:
                if re.search(pattern, msg.lower()):
                    matched_any = True
                    failures.append(f"❌ '{msg}' matches pattern '{pattern}'")
                    print(f"❌ '{msg}' matches '{pattern}' (SHOULD NOT)")
                    break
            
            if not matched_any:
                print(f"✅ '{msg}' - no completion match")
    
    # Test actual completion messages
    completion_messages = [
        "я сделал пробежку",
        "выполнил тренировку",
        "готово",
        "закончил с проектом",
    ]
    
    print("\nChecking completion messages DO match patterns:")
    for msg in completion_messages:
        if match:
            matched_any = False
            for pattern in patterns:
                if re.search(pattern, msg.lower()):
                    matched_any = True
                    print(f"✅ '{msg}' matches '{pattern}'")
                    break
            
            if not matched_any:
                failures.append(f"❌ '{msg}' doesn't match any pattern (SHOULD MATCH)")
                print(f"❌ '{msg}' - no match (SHOULD MATCH)")
    
    return failures


async def test_ai_integration():
    """Test actual AI responses"""
    print("\n=== TESTING AI INTEGRATION ===")
    print("(This will make actual API calls)")
    
    # Test a few critical cases
    critical_cases = [
        {
            "message": "я только с пробежки",
            "should_not_complete": True,
        },
        {
            "message": "создай задачу разбудить сына и погулять с собакой",
            "expected_tasks": 2,
        },
        {
            "message": "да создай задачу с таким заголовком",
            "should_reject": True,
        },
    ]
    
    print("\nWARNING: This will make API calls. Press Enter to continue or Ctrl+C to skip...")
    try:
        input()
    except KeyboardInterrupt:
        print("\nSkipped AI integration tests")
        return []
    
    failures = []
    for case in critical_cases:
        print(f"\nTesting: '{case['message']}'")
        try:
            response = await chat_with_ai(case['message'], user_id=TEST_USER_ID)
            print(f"Response: {response['content'][:100]}...")
            
            if 'tool_calls' in response:
                print(f"Tool calls: {len(response['tool_calls'])} calls")
                for call in response['tool_calls']:
                    func_name = call.get('function', {}).get('name', 'unknown')
                    print(f"  - {func_name}")
            
            # Add validation logic here based on case expectations
            
        except Exception as e:
            print(f"❌ Error: {e}")
            failures.append(f"Error testing '{case['message']}': {e}")
    
    return failures


def main():
    """Run all tests"""
    print("=" * 60)
    print("AGENT INTERPRETATION TEST SUITE")
    print("=" * 60)
    
    all_failures = []
    
    # Test 1: Task parsing
    failures = test_parse_multiple_tasks()
    all_failures.extend(failures)
    
    # Test 2: Quality filters
    failures = test_quality_filters()
    all_failures.extend(failures)
    
    # Test 3: Completion patterns
    failures = test_completion_patterns()
    all_failures.extend(failures)
    
    # Test 4: AI integration (optional)
    # failures = asyncio.run(test_ai_integration())
    # all_failures.extend(failures)
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    if all_failures:
        print(f"\n❌ FAILED: {len(all_failures)} issues found\n")
        for failure in all_failures:
            print(f"  {failure}")
    else:
        print("\n✅ ALL TESTS PASSED\n")
    
    return len(all_failures)


if __name__ == "__main__":
    exit_code = main()
    exit(exit_code)
