#!/usr/bin/env python3
"""Test extended time parsing patterns"""

import os
os.environ['LOCAL'] = '1'

from datetime import datetime
import pytz
from ai_integration.utils import parse_natural_time

# Test cases with various time formats
test_cases = [
    # Original patterns
    "завтра в 10:30",
    "к 10 часам",
    "8 утра",
    "6 вечера",
    "2 ночи",
    
    # New patterns
    "до 10 часов",
    "после 15 часов", 
    "в 14 часов",
    "около 9 часов",
    "примерно в 11 часов",
    "полдень",
    "полночь",
    
    # Real user phrases
    "подготовить отчет к 10 часам",
    "встреча до 15 часов",
    "позвонить около 14 часов",
    "завтра в полдень",
    "сегодня после 18 часов",
]

print("🧪 Тестирование расширенных паттернов времени\n")
print("=" * 70)

current_time = datetime.now(pytz.UTC)
print(f"Текущее время: {current_time.strftime('%Y-%m-%d %H:%M:%S')} UTC\n")

success_count = 0
fail_count = 0

for test_str in test_cases:
    result = parse_natural_time(test_str, current_time)
    
    if result:
        success_count += 1
        result_str = result.strftime('%Y-%m-%d %H:%M')
        status = "✅"
    else:
        fail_count += 1
        result_str = "НЕ РАСПОЗНАНО"
        status = "❌"
    
    print(f"{status} '{test_str:40s}' → {result_str}")

print("\n" + "=" * 70)
print(f"\n📊 Результаты:")
print(f"   ✅ Успешно: {success_count}/{len(test_cases)}")
print(f"   ❌ Ошибок: {fail_count}/{len(test_cases)}")

if fail_count == 0:
    print("\n🎉 Все тесты пройдены!")
else:
    print(f"\n⚠️  {fail_count} паттернов не распознаны")
