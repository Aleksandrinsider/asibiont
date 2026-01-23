#!/usr/bin/env python3
"""Test all API endpoints for syntax errors"""

import ast
import sys

def check_function(filename, func_name, start_line, end_line):
    """Check if a function has syntax errors"""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        # Get function code
        func_lines = lines[start_line-1:end_line]
        func_code = ''.join(func_lines)
        
        # Try to parse it
        ast.parse(func_code)
        print(f"✅ {func_name}: OK")
        return True
    except SyntaxError as e:
        print(f"❌ {func_name}: SYNTAX ERROR at line {e.lineno}")
        print(f"   {e.msg}")
        print(f"   {e.text}")
        return False
    except Exception as e:
        print(f"⚠️  {func_name}: {type(e).__name__}: {e}")
        return False

# Check critical API handlers
handlers = [
    ('main.py', 'api_tasks_handler', 4694, 4850),
    ('main.py', 'api_interactions_handler', 4879, 4950),
    ('main.py', 'api_profile_handler', 5045, 5150),
    ('main.py', 'get_feed_handler', 4188, 4350),
]

print("🔍 Checking API handlers for syntax errors...\n")

all_ok = True
for filename, func_name, start, end in handlers:
    if not check_function(filename, func_name, start, end):
        all_ok = False

if all_ok:
    print("\n✅ All handlers passed syntax check")
else:
    print("\n❌ Some handlers have syntax errors")
    sys.exit(1)
