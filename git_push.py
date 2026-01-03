import subprocess
import os

os.chdir(r'C:\Users\Insider\Desktop\Task')

commands = [
    ['git', 'add', 'main.py', 'static/style.css'],
    ['git', 'commit', '-m', 'Fix dashboard to use dashboard_new.html'],
    ['git', 'push', 'origin', 'main']
]

for cmd in commands:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        print(f"Command: {' '.join(cmd)}")
        print(f"Output: {result.stdout}")
        if result.stderr:
            print(f"Error: {result.stderr}")
        print()
    except Exception as e:
        print(f"Error running {cmd}: {e}")
