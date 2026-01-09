import requests
import json

# Проверяем API задач
try:
    response = requests.get('http://localhost:8000/api/tasks')
    print(f'Status: {response.status_code}')
    if response.status_code == 200:
        data = response.json()
        print(f'Tasks count: {len(data.get("tasks", []))}')
        for task in data.get('tasks', []):
            print(f'- {task["title"]} (ID: {task["id"]})')
    else:
        print(f'Error: {response.text}')
except Exception as e:
    print(f'Error: {e}')