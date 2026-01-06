"""Проверка API endpoint для задач"""
import requests
import json

# Эмуляция запроса к API
USER_ID = 146333757

print("Testing API endpoint...")
print(f"User telegram_id: {USER_ID}")
print()

# Проверим локальный сервер
try:
    # API tasks endpoint
    response = requests.get(
        f"http://localhost:5000/api/tasks/{USER_ID}",
        timeout=5
    )
    
    if response.status_code == 200:
        data = response.json()
        tasks = data.get('tasks', [])
        print(f"API Response: {response.status_code}")
        print(f"Tasks returned: {len(tasks)}")
        print()
        
        for task in tasks:
            print(f"Task ID: {task['id']}")
            print(f"  Title: {task['title']}")
            print(f"  Status: {task['status']}")
            print(f"  Is delegated: {task.get('is_delegated', False)}")
            print(f"  Delegated to: {task.get('delegated_to_username', 'None')}")
            print(f"  Reminder: {task.get('reminder_time', 'None')}")
            print()
    else:
        print(f"API Error: {response.status_code}")
        print(response.text)
        
except requests.exceptions.ConnectionError:
    print("ERROR: Cannot connect to localhost:5000")
    print("Make sure the server is running")
except Exception as e:
    print(f"ERROR: {e}")
