import requests
import json
import os
import subprocess
import time

# Тестирование агента на различных запросах

BASE_URL = "http://localhost:8001"

def test_agent():
    print("Starting server...")
    env = os.environ.copy()
    env["LOCAL"] = "1"
    env["SKIP_POLLING"] = "1"
    env["PORT"] = "8001"
    server = subprocess.Popen(["python", "main.py"], env=env)
    time.sleep(15)  # wait for server to start

    session = requests.Session()

    print("Testing /health...")
    response = session.get(f"{BASE_URL}/health")
    print(f"Health status: {response.status_code}")
    if response.status_code != 200:
        print(f"Health failed: {response.text}")
        server.terminate()
        return

    # 1. Логин
    print("1. Логин пользователя 111111...")
    response = session.get(f"{BASE_URL}/direct_login?user_id=111111")
    print(f"Login status: {response.status_code}")
    if response.status_code != 200:
        print(f"Login failed: {response.text}")
        server.terminate()
        return

    # 2. Тестовые запросы
    test_messages = [
        "привет",
        "покажи задачи",
        "добавь задачу проверить почту",
        "напомни через 5 минут проверить почту",
        "выполнил проверить почту",
        "найди людей с интересом к спорту",
        "живу в Москве, работаю в IT",
        "удали все задачи",
        "что ты умеешь?",
        "помоги с планированием дня"
    ]

    for i, message in enumerate(test_messages, 1):
        print(f"\n{i+1}. Тестирую: '{message}'")
        try:
            response = session.post(f"{BASE_URL}/chat", data={'message': message})
            print(f"Status: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                print(f"Response: {data.get('response', 'No response')[:200]}...")
            else:
                print(f"Error: {response.text}")
        except Exception as e:
            print(f"Exception: {e}")

    print("Stopping server...")
    server.terminate()
    server.wait()

if __name__ == "__main__":
    test_agent()