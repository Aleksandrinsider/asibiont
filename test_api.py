#!/usr/bin/env python3
"""Test script for API endpoints"""

import requests
import threading
import time
import subprocess
import sys
import os

def start_server():
    """Start the server in background"""
    os.chdir(r'c:\Users\Insider\Desktop\Task')
    process = subprocess.Popen([sys.executable, 'main.py'], 
                             stdout=subprocess.PIPE, 
                             stderr=subprocess.PIPE)
    return process

def test_api():
    """Test API endpoints"""
    time.sleep(5)  # Wait for server to start
    
    try:
        # Test health
        response = requests.get('http://localhost:8000/health', timeout=5)
        print(f'Health: {response.status_code} - {response.text}')
        
        # Test direct login
        response = requests.get('http://localhost:8000/direct_login?user_id=123456789', 
                              allow_redirects=False, timeout=5)
        print(f'Direct login: {response.status_code}')
        
        # Test profile API (should work with session)
        session = requests.Session()
        # First set session via direct login
        response = session.get('http://localhost:8000/direct_login?user_id=123456789', 
                             allow_redirects=False, timeout=5)
        print(f'Session login: {response.status_code}')
        
        # Now test profile API
        response = session.get('http://localhost:8000/api/profile', timeout=5)
        print(f'Profile API: {response.status_code}')
        if response.status_code == 200:
            print(f'Profile data: {response.json()}')
        else:
            print(f'Error: {response.text}')
            
    except Exception as e:
        print(f'Error: {e}')

if __name__ == '__main__':
    print("Starting server...")
    server_process = start_server()
    
    try:
        test_api()
    finally:
        print("Stopping server...")
        server_process.terminate()
        server_process.wait()</content>
<parameter name="filePath">c:\Users\Insider\Desktop\Task\test_api.py