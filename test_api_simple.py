import requests
import json

# Test the health endpoint
try:
    response = requests.get('http://localhost:8080/health')
    print(f"Health endpoint status: {response.status_code}")
    print(f"Health response: {response.text}")
except Exception as e:
    print(f"Error connecting to health endpoint: {e}")

# Test the partners API without authentication (should return 401)
try:
    response = requests.get('http://localhost:8080/api/partners')
    print(f"Partners API status: {response.status_code}")
    print(f"Partners response: {response.text}")
except Exception as e:
    print(f"Error connecting to partners API: {e}")