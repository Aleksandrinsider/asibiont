#!/usr/bin/env python
# -*- coding: utf-8 -*-

filename = 'test_results_15turns.txt'

# Try different encodings
try:
    with open(filename, 'r', encoding='utf-16') as f:
        lines = f.readlines()
    print("✅ Read with utf-16 encoding")
except:
    try:
        with open(filename, 'r', encoding='cp1251') as f:
            lines = f.readlines()
        print("✅ Read with cp1251 encoding")
    except:
        with open(filename, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        print("✅ Read with utf-8 encoding")

content = ''.join(lines)

print("="*70)
print("ПРОСТОЙ АНАЛИЗ ТЕСТА")
print("="*70)
print()

turns = sum(1 for l in lines if '[HOD' in l)
not_call = sum(1 for l in lines if 'AI did NOT call' in l)
add_task = sum(1 for l in lines if '[ADD_TASK]' in l)
update_prof = sum(1 for l in lines if '[UPDATE_PROFILE]' in l)
find_partners = sum(1 for l in lines if '[FIND_PARTNERS]' in l)
reschedule = sum(1 for l in lines if '[RESCHEDULE_TASK]' in l)
hybrid_miss = sum(1 for l in lines if 'HYBRID REQUIRED' in l and 'did not call' in l)

print(f"Turns completed: {turns}")
print(f"\n❌ AI did NOT call tools: {not_call}")
print(f"❌ HYBRID REQUIRED tool  miss: {hybrid_miss}")
print()
print(f"✅ Tool executions:")
print(f"   ADD_TASK: {add_task}")
print(f"   UPDATE_PROFILE: {update_prof}")
print(f"   FIND_PARTNERS: {find_partners}")
print(f"   RESCHEDULE_TASK: {reschedule}")
print()

# Find report
if 'Zadachi sozdany:' in content:
    import re
    match = re.search(r'Zadachi sozdany:\s*(\d+)', content)
    if match:
        print(f"📝 FINAL REPORT:")
        print(f"   Tasks created: {match.group(1)}")

if 'Profil obnovlen' in content:
    print(f"   Profile: ✅ Updated")

print()
print("="*70)
print("ПРОБЛЕМЫ И РЕКОМЕНДАЦИИ:")
print("="*70)
print()

if not_call > 0:
    print(f"⚠️  {not_call} случаев когда AI не вызвал инструменты")
    print(f"   Причины могут быть:")
    print(f"   - Общий разговор (нормально)")
    print(f"   - Приветствия (нормально)")
    print(f"   - AI запрашивает уточнения (нормально)")
    print()

if hybrid_miss > 0:
    print(f"❌ {hybrid_miss} случаев когда AI НЕ вызвал ОБЯЗАТЕЛЬНЫЙ инструмент")
    print(f"   ЭТО ПРОБЛЕМА - нужно улучшить:")
    print(f"   - Более четкие инструкции в промпте")
    print(f"   - Лучшие примеры использования")
    print(f"   - Усиленный tool_choice для критичных команд")
    print()

if turns > 0:
    print(f"\n📊 Miss rate: {not_call}/{turns} = {(not_call/turns*100):.1f}% (включая оправданные пропуски)")
    if hybrid_miss > 0:
        print(f"❌ CRITICAL miss rate: {hybrid_miss}/{turns} = {(hybrid_miss/turns*100):.1f}% (только обязательные пропуски)")
else:
    print("\n⚠️  No turns found - check file encoding or test completion")

print()
print("="*70)
