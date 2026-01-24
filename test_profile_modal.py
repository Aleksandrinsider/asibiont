#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тест проверки профиля и модального окна
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import Session, User, UserProfile
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Тестируем вашего пользователя (подставьте ваш telegram_id)
USER_TELEGRAM_ID = 7451460638  # Ваш telegram_id

def check_profile():
    """Проверяем состояние профиля"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=USER_TELEGRAM_ID).first()
        if not user:
            print(f"❌ Пользователь {USER_TELEGRAM_ID} не найден")
            return
        
        print(f"✅ Пользователь найден: @{user.username}")
        
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            print("❌ Профиль не найден в базе")
            return
        
        print("\n📋 Текущее состояние профиля:")
        print(f"  Город: '{profile.city}' (type: {type(profile.city).__name__}, repr: {repr(profile.city)})")
        print(f"  Интересы: '{profile.interests}' (type: {type(profile.interests).__name__}, repr: {repr(profile.interests)})")
        print(f"  Компания: '{profile.company}' (type: {type(profile.company).__name__}, repr: {repr(profile.company)})")
        print(f"  Должность: '{profile.position}' (type: {type(profile.position).__name__}, repr: {repr(profile.position)})")
        print(f"  Навыки: '{profile.skills}' (type: {type(profile.skills).__name__}, repr: {repr(profile.skills)})")
        print(f"  Цели: '{profile.goals}' (type: {type(profile.goals).__name__}, repr: {repr(profile.goals)})")
        
        # Проверяем условия показа модального окна
        def is_empty_field(value):
            return not value or (isinstance(value, str) and not value.strip())
        
        print("\n🔍 Анализ полей для модального окна:")
        
        city_empty = is_empty_field(profile.city)
        interests_empty = is_empty_field(profile.interests)
        
        print(f"  Город пустой: {city_empty}")
        print(f"  Интересы пустые: {interests_empty}")
        
        should_show_modal = city_empty or interests_empty
        
        print(f"\n{'🔴' if should_show_modal else '🟢'} Модальное окно {'ДОЛЖНО ПОКАЗЫВАТЬСЯ' if should_show_modal else 'НЕ ДОЛЖНО ПОКАЗЫВАТЬСЯ'}")
        
        if should_show_modal:
            print("\n💡 Рекомендация:")
            if city_empty:
                print("  - Заполните город")
            if interests_empty:
                print("  - Заполните интересы")
        
    finally:
        session.close()


def simulate_save(city="", interests=""):
    """Симулируем сохранение данных как из формы"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=USER_TELEGRAM_ID).first()
        if not user:
            print(f"❌ Пользователь {USER_TELEGRAM_ID} не найден")
            return
        
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            profile = UserProfile(user_id=user.id)
            session.add(profile)
        
        print(f"\n📝 Симуляция сохранения:")
        print(f"  Входные данные - Город: '{city}', Интересы: '{interests}'")
        
        # Применяем ту же логику что и в main.py
        if city and city.strip():
            profile.city = city.strip()
            print(f"  ✅ Город сохранён: '{profile.city}'")
        else:
            print(f"  ⚠️ Город НЕ сохранён (пустая строка)")
        
        if interests and interests.strip():
            profile.interests = interests.strip()
            print(f"  ✅ Интересы сохранены: '{profile.interests}'")
        else:
            print(f"  ⚠️ Интересы НЕ сохранены (пустая строка)")
        
        # НЕ COMMIT - только показываем что будет
        print("\n⚠️ Изменения НЕ сохранены в базу (режим симуляции)")
        
    finally:
        session.close()


if __name__ == "__main__":
    print("="*80)
    print("ПРОВЕРКА ПРОФИЛЯ И МОДАЛЬНОГО ОКНА")
    print("="*80)
    
    check_profile()
    
    print("\n" + "="*80)
    print("СИМУЛЯЦИЯ СОХРАНЕНИЯ")
    print("="*80)
    
    print("\n1️⃣ Попытка сохранить пустые строки:")
    simulate_save(city="", interests="")
    
    print("\n2️⃣ Попытка сохранить строки из пробелов:")
    simulate_save(city="   ", interests="   ")
    
    print("\n3️⃣ Попытка сохранить нормальные данные:")
    simulate_save(city="Москва", interests="программирование")
    
    print("\n" + "="*80)
    print("\n✅ Проверка завершена! Изменения в базу не внесены.")
