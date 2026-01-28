import asyncio
import os
os.environ['LOCAL'] = '1'

from ai_integration.chat import chat_with_ai
from models import Session, User, Task

TEST_ID = 99666

def check(r, tool):
    tools = r.get('tool_calls', []) if isinstance(r, dict) else []
    return any(tool in str(t) for t in tools)

async def test():
    s = Session()
    s.query(Task).filter_by(user_id=TEST_ID).delete()
    s.query(User).filter_by(telegram_id=TEST_ID).delete()
    s.commit()
    u = User(telegram_id=TEST_ID, username="test")
    s.add(u)
    s.commit()
    s.close()
    
    tests = [
        ("add_task", "Напомни позвонить завтра"),
        ("list_tasks", "Покажи задачи"),
        ("complete_task", "Готово"),
        ("add_task", "Напомни купить хлеб"),
        ("update_profile", "Я из Москвы"),
        ("update_profile", "Работаю программистом"),
        ("reschedule_task", "Перенеси хлеб на послезавтра"),
        ("delete_task", "Удали хлеб"),
        ("delegate_task", "Делегируй Ивану проверить отчет"),
        ("set_recurring_task", "Напоминай делать зарядку каждый день"),
        ("find_partners", "Найди партнеров"),
        ("delete_all_tasks", "Удали все задачи"),
    ]
    
    results = []
    for i, (tool, msg) in enumerate(tests, 1):
        print(f"\n{i}. {tool}: {msg[:30]}...")
        try:
            r = await chat_with_ai(msg, user_id=TEST_ID)
            if check(r, tool):
                print(f"   [OK]")
                results.append(True)
            else:
                print(f"   [FAIL] - got: {r.get('tool_calls', [])[:1]}")
                results.append(False)
        except Exception as e:
            print(f"   [ERROR] {e}")
            results.append(False)
    
    s = Session()
    s.query(Task).filter_by(user_id=TEST_ID).delete()
    s.query(User).filter_by(telegram_id=TEST_ID).delete()
    s.commit()
    s.close()
    
    print(f"\n{'='*50}")
    print(f"RESULT: {sum(results)}/{len(results)} passed")
    print(f"{'='*50}")
    return all(results)

asyncio.run(test())
