"""
Comprehensive test of all agent functions
"""
import asyncio
import os
os.environ['LOCAL'] = '1'

from ai_integration.chat import chat_with_ai
from models import Session, User, Task

TEST_USER_ID = 99777

def check_tool_call(response, tool_name):
    """Check if specific tool was called"""
    tools = response.get('tool_calls', []) if isinstance(response, dict) else []
    return any(tool_name in str(t) for t in tools)

async def test_function(name, message, expected_tool=None):
    """Test a single function"""
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print(f"Message: {message}")
    print(f"{'='*60}")
    
    try:
        response = await chat_with_ai(message, user_id=TEST_USER_ID)
        
        if expected_tool:
            if check_tool_call(response, expected_tool):
                print(f"[OK] {expected_tool} called")
                return True
            else:
                print(f"[FAIL] {expected_tool} NOT called")
                tools = response.get('tool_calls', []) if isinstance(response, dict) else []
                print(f"Tools called: {[str(t) for t in tools]}")
                return False
        else:
            print(f"[OK] Response received")
            return True
    except Exception as e:
        print(f"[ERROR] {e}")
        return False

async def main():
    # Cleanup
    print("Cleanup...")
    s = Session()
    s.query(Task).filter_by(user_id=TEST_USER_ID).delete()
    s.query(User).filter_by(telegram_id=TEST_USER_ID).delete()
    s.commit()
    
    # Create user
    u = User(telegram_id=TEST_USER_ID, username="test_user")
    s.add(u)
    s.commit()
    s.close()
    
    results = []
    
    # 1. CREATE TASK
    results.append(await test_function(
        "Create task with time",
        "Напомни позвонить клиенту завтра в 10:00",
        "add_task"
    ))
    
    # 2. LIST TASKS
    results.append(await test_function(
        "List tasks",
        "Покажи мои задачи",
        "list_tasks"
    ))
    
    # 3. CREATE ANOTHER TASK
    results.append(await test_function(
        "Create task without time",
        "Напомни купить молоко",
        "add_task"
    ))
    
    # 4. COMPLETE BY CONTEXT
    results.append(await test_function(
        "Complete by context (last task)",
        "Готово",
        "complete_task"
    ))
    
    # 5. UPDATE PROFILE - CITY
    results.append(await test_function(
        "Update profile - city",
        "Я из Москвы",
        "update_profile"
    ))
    
    # 6. UPDATE PROFILE - SKILLS
    results.append(await test_function(
        "Update profile - skills",
        "Работаю программистом",
        "update_profile"
    ))
    
    # 7. UPDATE PROFILE - INTERESTS
    results.append(await test_function(
        "Update profile - interests",
        "Люблю музыку и спорт",
        "update_profile"
    ))
    
    # 8. RESCHEDULE TASK
    results.append(await test_function(
        "Reschedule task",
        "Перенеси звонок клиенту на послезавтра",
        "reschedule_task"
    ))
    
    # 9. FIND PARTNERS
    results.append(await test_function(
        "Find partners",
        "Найди партнеров для проекта",
        "find_partners"
    ))
    
    # 10. CREATE TASK FOR DELEGATION
    results.append(await test_function(
        "Create task to delegate",
        "Напомни проверить код",
        "add_task"
    ))
    
    # 11. DELEGATE TASK
    results.append(await test_function(
        "Delegate task",
        "Делегируй Ивану проверку кода",
        "delegate_task"
    ))
    
    # 12. RECURRING TASK
    results.append(await test_function(
        "Set recurring task",
        "Напоминай делать зарядку каждый день в 8:00",
        "set_recurring_task"
    ))
    
    # 13. GET TASK DETAILS
    results.append(await test_function(
        "Get task details",
        "Покажи детали задачи про звонок",
        "get_task_details"
    ))
    
    # 14. DELETE SPECIFIC TASK
    results.append(await test_function(
        "Delete specific task",
        "Удали задачу про зарядку",
        "delete_task"
    ))
    
    # 15. DELETE BY CONTEXT
    results.append(await test_function(
        "Delete by context (if works)",
        "Удали",
        None  # May ask for clarification
    ))
    
    # 16. DELETE ALL TASKS
    results.append(await test_function(
        "Delete all tasks",
        "Удали все задачи",
        "delete_all_tasks"
    ))
    
    # Cleanup
    s = Session()
    s.query(Task).filter_by(user_id=TEST_USER_ID).delete()
    s.query(User).filter_by(telegram_id=TEST_USER_ID).delete()
    s.commit()
    s.close()
    
    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    passed = sum(results)
    total = len(results)
    print(f"Passed: {passed}/{total}")
    print(f"Success rate: {passed/total*100:.1f}%")
    
    if passed == total:
        print("\n[SUCCESS] All tests passed!")
    else:
        print(f"\n[WARNING] {total-passed} tests failed")
    
    return passed == total

if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)
