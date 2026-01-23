#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Отладочный тест для проверки обновления профиля
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import SessionLocal, User, UserProfile
import asyncio
import logging

# Включаем подробное логирование
logging.basicConfig(level=logging.INFO)

async def test_profile_updates():
    db = SessionLocal()
    user_id = 999999
    
    # Очистка профиля
    print("\n" + "="*60)
    print("ОЧИСТКА ПРОФИЛЯ")
    print("="*60)
    user = db.query(User).filter_by(telegram_id=user_id).first()
    if user:
        profile = db.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            profile.city = None
            profile.company = None
            profile.position = None
            profile.interests = None
            profile.skills = None
            profile.goals = None
            db.commit()
            print("Профиль очищен")
    
    # Тест 1: Явное обновление профиля
    print("\n" + "="*60)
    print("ТЕСТ 1: Явное обновление профиля")
    print("="*60)
    print("Запрос: 'обнови профиль: я из Москвы, работаю программистом'")
    
    response1 = await chat_with_ai(
        message="обнови профиль: я из Москвы, работаю программистом",
        user_id=user_id,
        db_session=db
    )
    
    print(f"\nОтвет агента:\n{response1[:300]}...")
    
    # Проверяем профиль (перезапрашиваем из БД)
    user = db.query(User).filter_by(telegram_id=user_id).first()
    profile = db.query(UserProfile).filter_by(user_id=user.id).first()
    if profile:
        print("\nДанные профиля после обновления:")
        print(f"  City: {profile.city}")
        print(f"  Company: {profile.company}")
        print(f"  Position: {profile.position}")
        print(f"  Skills: {profile.skills}")
        print(f"  Interests: {profile.interests}")
    
    # Тест 2: Неявное обновление через информацию
    print("\n" + "="*60)
    print("ТЕСТ 2: Неявное обновление через информацию")
    print("="*60)
    print("Запрос: 'я работаю data engineer в Яндексе, навыки: Python, SQL, Airflow'")
    
    response2 = await chat_with_ai(
        message="я работаю data engineer в Яндексе, навыки: Python, SQL, Airflow",
        user_id=user_id,
        db_session=db
    )
    
    print(f"\nОтвет агента:\n{response2[:300]}...")
    
    # Проверяем профиль (перезапрашиваем из БД)
    user = db.query(User).filter_by(telegram_id=user_id).first()
    profile = db.query(UserProfile).filter_by(user_id=user.id).first()
    if profile:
        print("\nДанные профиля после обновления:")
        print(f"  City: {profile.city}")
        print(f"  Company: {profile.company}")
        print(f"  Position: {profile.position}")
        print(f"  Skills: {profile.skills}")
        print(f"  Interests: {profile.interests}")
    
    # Тест 3: Обновление через естественный разговор
    print("\n" + "="*60)
    print("ТЕСТ 3: Обновление через естественный разговор")
    print("="*60)
    print("Запрос: 'давай заполним профиль: живу в Санкт-Петербурге, увлекаюсь машинным обучением'")
    
    response3 = await chat_with_ai(
        message="давай заполним профиль: живу в Санкт-Петербурге, увлекаюсь машинным обучением",
        user_id=user_id,
        db_session=db
    )
    
    print(f"\nОтвет агента:\n{response3[:300]}...")
    
    # Проверяем профиль (перезапрашиваем из БД)
    user = db.query(User).filter_by(telegram_id=user_id).first()
    profile = db.query(UserProfile).filter_by(user_id=user.id).first()
    if profile:
        print("\nДанные профиля после обновления:")
        print(f"  City: {profile.city}")
        print(f"  Company: {profile.company}")
        print(f"  Position: {profile.position}")
        print(f"  Skills: {profile.skills}")
        print(f"  Interests: {profile.interests}")
    
    # Тест 4: Просто информация о себе
    print("\n" + "="*60)
    print("ТЕСТ 4: Просто информация о себе")
    print("="*60)
    print("Запрос: 'я аналитик данных, работаю с Tableau и Power BI'")
    
    response4 = await chat_with_ai(
        message="я аналитик данных, работаю с Tableau и Power BI",
        user_id=user_id,
        db_session=db
    )
    
    print(f"\nОтвет агента:\n{response4[:300]}...")
    
    # Проверяем профиль (перезапрашиваем из БД)
    user = db.query(User).filter_by(telegram_id=user_id).first()
    profile = db.query(UserProfile).filter_by(user_id=user.id).first()
    if profile:
        print("\nДанные профиля после обновления:")
        print(f"  City: {profile.city}")
        print(f"  Company: {profile.company}")
        print(f"  Position: {profile.position}")
        print(f"  Skills: {profile.skills}")
        print(f"  Interests: {profile.interests}")
    
    # Итоговый отчет
    print("\n" + "="*60)
    print("ИТОГОВЫЙ ОТЧЕТ")
    print("="*60)
    
    profile = db.query(UserProfile).filter_by(user_id=user.id).first()
    if profile:
        filled_fields = sum([
            bool(profile.city),
            bool(profile.company),
            bool(profile.position),
            bool(profile.skills),
            bool(profile.interests)
        ])
        print(f"Заполнено полей: {filled_fields}/5")
        print("\nФинальное состояние профиля:")
        print(f"  City: {profile.city or '[НЕ ЗАПОЛНЕНО]'}")
        print(f"  Company: {profile.company or '[НЕ ЗАПОЛНЕНО]'}")
        print(f"  Position: {profile.position or '[НЕ ЗАПОЛНЕНО]'}")
        print(f"  Skills: {profile.skills or '[НЕ ЗАПОЛНЕНО]'}")
        print(f"  Interests: {profile.interests or '[НЕ ЗАПОЛНЕНО]'}")
        
        if filled_fields >= 3:
            print("\n[OK] Профиль обновляется хорошо")
        elif filled_fields >= 1:
            print("\n[WARNING] Профиль обновляется частично")
        else:
            print("\n[ERROR] Профиль не обновляется")
    
    db.close()

if __name__ == "__main__":
    asyncio.run(test_profile_updates())
