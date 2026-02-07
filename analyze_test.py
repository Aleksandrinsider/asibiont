#!/usr/bin/env python
# -*- coding: utf-8 -*-
import re
import os
import sys

# Check both filenames
filenames = ['test_results_15turns.txt', 'test_with_proactive_context.txt']
filename = None

for fname in filenames:
    if os.path.exists(fname):
        filename = fname
        break

if filename is None:
    print(f"❌ No test result files found. Test might still be running...")
    exit(1)

print(f"📄 Analyzing file: {filename}")
print()

if not os.path.exists(filename):
    print(f"❌ File {filename} not found. Test might still be running...")
    exit(1)

# Try different encodings
content = None
encoding_used = None
for encoding in ['utf-8', 'cp1251', 'latin-1', 'utf-16']:
    try:
        with open(filename, 'r', encoding=encoding, errors='ignore') as f:
            content = f.read()
        encoding_used = encoding
        print(f"✅ Successfully read file with {encoding} encoding")
        break
    except Exception as e:
        continue

if content is None:
    print("❌ Could not read file with any encoding")
    exit(1)

# Convert to clean UTF-8 for regex
if encoding_used == 'cp1251':
    # Re-encode to clean format
    try:
        with open(filename, 'r', encoding='cp1251', errors='ignore') as f:
            raw = f.read()
        # Replace specific patterns that are known
        content = raw
    except:
        pass

print("=" * 70)
print("TEST ANALYSIS REPORT")
print("=" * 70)

# Statistics - use simple string counting instead of regex
lines = content.split('\n')
turns = content.count('HOD ')  # Use Russian "HOD" instead of "TURN"
not_called = content.count('AI did NOT call')
add_task = content.count('[ADD_TASK]')
update_profile = content.count('[UPDATE_PROFILE]')
find_partners = content.count('[FIND_PARTNERS]')
list_tasks = content.count('[LIST_TASKS]')
complete_task = content.count('[COMPLETE_TASK]')
analyze_tasks = content.count('[ANALYZE_TASKS]')

# Count tasks created from REPORT section
задачи_созданы = 0
if 'Zadachi sozdany:' in content:
    # Extract number after "Zadachi sozdany:"
    match = re.search(r'Zadachi sozdany:\s*(\d+)', content)
    if match:
        задачи_созданы = int(match.group(1))

# Profile updated?
profile_updated = 'PROFILE] Profil obnovlen' in content or 'UPDATE_PROFILE' in content

print(f"\n📊 STATISTICS:")
print(f"   Turns completed: {turns}")
print(f"   Total lines: {len(lines)}")
print(f"   Total characters: {len(content)}")
print(f"   \n❌ Missed tool calls: {not_called}")
print(f"   \n✅ Tool calls made:")
print(f"      ADD_TASK: {add_task}")
print(f"      UPDATE_PROFILE: {update_profile}")
print(f"      FIND_PARTNERS: {find_partners}")
print(f"      LIST_TASKS: {list_tasks}")
print(f"      COMPLETE_TASK: {complete_task}")
print(f"      ANALYZE_TASKS: {analyze_tasks}")
print(f"\n   📝 Final Report:")
print(f"      Tasks created (from report): {задачи_созданы}")
print(f"      Profile updated: {'✅ Yes' if profile_updated else '❌ No'}")

if turns > 0:
    miss_rate = (not_called / turns) * 100
    print(f"\n📈 Miss rate: {miss_rate:.1f}% ({not_called}/{turns})")
else:
    print(f"\n⚠️  No complete turns found yet - test might be in progress")

# Find instances where AI did NOT call tools
print(f"\n\n{'=' * 70}")
print("MISSED TOOL CALL CONTEXTS (first 5 examples):")
print("=" * 70)

not_called_matches = list(re.finditer(r'AI did NOT call.*?\n.*?AI response content: (.*?)(?=\[|$)', content, re.DOTALL))
for i, match in enumerate(not_called_matches[:5], 1):
    response = match.group(1).strip()[:200]
    print(f"\n{i}. Response: {response}...")

# Show last 80 lines of output
print(f"\n\n{'=' * 70}")
print("LAST 80 LINES OF TEST OUTPUT:")
print("=" * 70)
lines = content.split('\n')
for line in lines[-80:]:
    print(line)
