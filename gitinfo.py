import subprocess, sys

cwd = r"C:\Users\Insider\Desktop\Task"

# Check if test files are tracked
r = subprocess.run(["git", "ls-files", "tests/test_full_suite.py", "tests/test_adaptability.py"], 
                   capture_output=True, text=True, cwd=cwd)
print("tracked files:")
print(r.stdout if r.stdout.strip() else "(none tracked)")

# Recent commits
r2 = subprocess.run(["git", "log", "--oneline", "-8"], capture_output=True, text=True, cwd=cwd)
print("\nrecent commits:")
print(r2.stdout)

# HEAD stat
r3 = subprocess.run(["git", "show", "--stat", "HEAD"], capture_output=True, text=True, cwd=cwd)
print("HEAD stat:")
print(r3.stdout[:600])
