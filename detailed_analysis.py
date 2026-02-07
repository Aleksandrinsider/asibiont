#!/usr/bin/env python
# -*- coding: utf-8 -*-
import re

with open('test_results_15turns.txt', 'r', encoding='utf-16') as f:
    content = f.read()

print('='*70)
print('КОНКРЕТНЫЕ ПРИМЕРЫ ПРОПУЩЕННЫХ ВЫЗОВОВ')
print('='*70)
print()

# Find all "HYBRID REQUIRED" misses  
print("❌ CRITICAL: HYBRID REQUIRED (обязательные инструменты пропущены):")
print()
req_matches = list(re.finditer(r'\[HYBRID REQUIRED\] AI did not call required tool (\w+)', content))
for i, m in enumerate(req_matches, 1):
    tool_name = m.group(1)
    # Find context around the miss
    start = max(0, m.start() - 300)
    end = min(len(content), m.end() + 500)
    context = content[start:end]
    
    print(f"{i}. Required tool: {tool_name}")
    # Extract user message and AI response
    user_match = re.search(r'\[USER\].*?:\s*(.*?)(?=\[\.\.\.|\[BOT\])', context, re.DOTALL)
    ai_match = re.search(r'AI response content: (.*?)(?=\[|$)', context, re.DOTALL)
    
    if user_match:
        print(f"   User said: {user_match.group(1).strip()[:150]}")
    if ai_match:
        print(f"   AI response: {ai_match.group(1).strip()[:150]}")
    print()

print()
print('='*70)
print('GENERAL MISSES (могут быть оправданы):')
print('='*70)
print()

# Find general "did NOT call" but not "HYBRID REQUIRED"
general_matches = []
for match in re.finditer(r'\[HYBRID\] AI did NOT call any tools.*?AI response content: (.*?)(?=\[|===)', content, re.DOTALL):
    # Check if this is followed by HYBRID REQUIRED
    next_100 = content[match.end():match.end()+100]
    if 'HYBRID REQUIRED' not in next_100:
        general_matches.append(match)

print(f"Found {len(general_matches)} general misses (first 5 shown):")
print()

for i, m in enumerate(general_matches[:5], 1):
    response = m.group(1).strip()
    if len(response) > 180:
        response = response[:180] + "..."
    print(f"{i}. AI Response: {response}")
    print()

print()
print('='*70)
print('RECOMMENDATIONS:')
print('='*70)
print()

if len(req_matches) > 0:
    print(f"⚠️  {len(req_matches)} CRITICAL misses need fixing:")
    print("   1. Strengthen routing logic for required tools")
    print("   2. Add more explicit examples in prompts")
    print("   3. Use tool_choice='required' for critical commands")
    print()

if len(general_matches) > 5:
    print(f"⚠️  {len(general_matches)} general misses - analyze if justified:")
    print("   - Check if responses are clarification questions (OK)")
    print("   - Check if responses are greetings/social (OK)")
    print("   - If action-oriented responses without tools = BAD")
    print()

tools_used = {
    'ADD_TASK': content.count('[ADD_TASK]'),
    'LIST_TASKS': content.count('[LIST_TASKS]'),
    'FIND_PARTNERS': content.count('[FIND_PARTNERS]'),
    'UPDATE_PROFILE': content.count('[UPDATE_PROFILE]'),
    'COMPLETE_TASK': content.count('[COMPLETE_TASK]'),
}

print("📊 Tool usage diversity:")
for tool, count in tools_used.items():
    status = "✅ Good" if count > 0 else "❌ Never used"
    print(f"   {tool}: {count} times - {status}")
