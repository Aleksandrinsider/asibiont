#!/usr/bin/env python3
import requests
import time

# Wait for server to start
time.sleep(3)

try:
    response = requests.post(
        'http://localhost:8080/admin/clear_database',
        headers={'X-Admin-Secret': 'aj00yr34Pmg9YM8gWSggYoCMSG8t1a6ahntl4OJyPcw'}
    )
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")
except Exception as e:
    print(f"Error: {e}")