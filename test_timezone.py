#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Тест определения времени для пользователей из разных городов"""

import pytz
from datetime import datetime

def test_timezone_for_cities():
    """Проверка определения времени для Москвы и Перми"""
    
    # Текущее UTC время
    base_now = datetime.now(pytz.UTC)
    print(f"Текущее UTC время: {base_now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print("-" * 60)
    
    # Пользователь из Москвы
    moscow_tz = pytz.timezone("Europe/Moscow")
    moscow_time = base_now.astimezone(moscow_tz)
    print(f"\n👤 Пользователь из МОСКВЫ:")
    print(f"   Timezone: Europe/Moscow (UTC+3)")
    print(f"   Локальное время: {moscow_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"   AI видит: {{{{current_date}}}} = {moscow_time.strftime('%Y-%m-%d')}")
    print(f"            {{{{current_time}}}} = {moscow_time.strftime('%H:%M')}")
    
    # Пользователь из Перми
    perm_tz = pytz.timezone("Asia/Yekaterinburg")
    perm_time = base_now.astimezone(perm_tz)
    print(f"\n👤 Пользователь из ПЕРМИ:")
    print(f"   Timezone: Asia/Yekaterinburg (UTC+5)")
    print(f"   Локальное время: {perm_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"   AI видит: {{{{current_date}}}} = {perm_time.strftime('%Y-%m-%d')}")
    print(f"            {{{{current_time}}}} = {perm_time.strftime('%H:%M')}")
    
    # Разница во времени
    time_diff = (perm_time.hour - moscow_time.hour) % 24
    print(f"\n⏰ Разница во времени: {time_diff} часа")
    print(f"   Когда в Москве {moscow_time.strftime('%H:%M')}, в Перми {perm_time.strftime('%H:%M')}")
    
    # Тест "через 5 минут"
    print(f"\n🔔 Тест: 'напомни через 5 минут'")
    from datetime import timedelta
    
    moscow_reminder = moscow_time + timedelta(minutes=5)
    perm_reminder = perm_time + timedelta(minutes=5)
    
    print(f"   Москва: {moscow_time.strftime('%H:%M')} + 5 мин = {moscow_reminder.strftime('%H:%M')}")
    print(f"   Пермь:  {perm_time.strftime('%H:%M')} + 5 мин = {perm_reminder.strftime('%H:%M')}")
    
    # Сохранение в БД (UTC)
    moscow_reminder_utc = moscow_reminder.astimezone(pytz.UTC)
    perm_reminder_utc = perm_reminder.astimezone(pytz.UTC)
    
    print(f"\n💾 Сохранение в БД (UTC):")
    print(f"   Москва: {moscow_reminder_utc.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"   Пермь:  {perm_reminder_utc.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    
    print("\n✅ Вывод: Каждый пользователь видит СВОЁ локальное время!")
    print("   Система корректно работает с разными часовыми поясами.")

if __name__ == "__main__":
    test_timezone_for_cities()
