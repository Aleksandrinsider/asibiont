#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тест улучшенной логики рекомендаций контактов
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from models import Session, User, UserProfile
from ai_integration.handlers import find_relevant_contacts_for_task

def test_contact_recommendations():
    """Тестируем улучшенную логику поиска контактов"""

    session = Session()

    try:
        # Найдем тестового пользователя
        user = session.query(User).filter_by(telegram_id=1).first()
        if not user:
            print("Тестовый пользователь не найден")
            return

        # Удалим существующие тестовые контакты
        session.query(UserProfile).filter(UserProfile.user_id.in_(
            session.query(User.id).filter(User.telegram_id.in_([1001, 1002, 1003]))
        )).delete(synchronize_session=False)
        session.query(User).filter(User.telegram_id.in_([1001, 1002, 1003])).delete()
        session.commit()

        # Создадим тестовые контакты
        print("Создаем тестовые контакты...")

        # Контакт 1: тренер по спорту
        contact1_user = User(username="trainer_alex", telegram_id=1001)
        session.add(contact1_user)
        session.flush()

        contact1_profile = UserProfile(
            user_id=contact1_user.id,
            skills="тренер, фитнес, спорт, йога, бег",
            interests="здоровье, спорт, питание",
            goals="помогать людям заниматься спортом, развивать фитнес студию",
            city="Москва"
        )
        session.add(contact1_profile)

        # Контакт 2: бизнесмен
        contact2_user = User(username="business_max", telegram_id=1002)
        session.add(contact2_user)
        session.flush()

        contact2_profile = UserProfile(
            user_id=contact2_user.id,
            skills="маркетинг, продажи, бизнес, предпринимательство, партнерская сеть",
            interests="стартапы, инвестиции, финансы",
            goals="заработать, развить партнерскую сеть, создать успешный бизнес",
            city="Москва"
        )
        session.add(contact2_profile)

        # Контакт 3: программист
        contact3_user = User(username="coder_anna", telegram_id=1003)
        session.add(contact3_user)
        session.flush()

        contact3_profile = UserProfile(
            user_id=contact3_user.id,
            skills="программирование, python, ai, машинное обучение",
            interests="технологии, разработка, стартапы",
            goals="создать ai продукт, найти команду для стартапа",
            city="Санкт-Петербург"
        )
        session.add(contact3_profile)

        # Создадим профиль для пользователя
        user_profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not user_profile:
            user_profile = UserProfile(
                user_id=user.id,
                skills="программирование, python",
                interests="спорт, бизнес",
                goals="заработать, заняться спортом",
                city="Москва"
            )
            session.add(user_profile)
        else:
            # Обновим существующий профиль
            user_profile.skills = "программирование, python"
            user_profile.interests = "спорт, бизнес"
            user_profile.goals = "заработать, заняться спортом"
            user_profile.city = "Москва"
            print("Обновлен существующий профиль пользователя")
        session.commit()

        # Проверим партнеров
        from ai_integration.handlers import get_partners_list
        partners = get_partners_list(user_id=user.telegram_id, session=session)
        print(f"Найдено партнеров: {len(partners)}")
        for p in partners:
            print(f"  - @{session.query(User).filter_by(id=p.user_id).first().username}: {p.city}, skills: {p.skills}, interests: {p.interests}")
        print()

        print("Тестируем рекомендации контактов...\n")
        print("=== Тест 1: 'хочу заработать' ===")
        result1 = find_relevant_contacts_for_task("хочу заработать", user_id=user.telegram_id, session=session)
        print(result1)
        print()

        # Тест 2: "хочу заняться спортом"
        print("=== Тест 2: 'хочу заняться спортом' ===")
        result2 = find_relevant_contacts_for_task("хочу заняться спортом", user_id=user.telegram_id, session=session)
        print(result2)
        print()

        # Тест 3: "развить партнерскую сеть"
        print("=== Тест 3: 'развить партнерскую сеть' ===")
        result3 = find_relevant_contacts_for_task("развить партнерскую сеть", user_id=user.telegram_id, session=session)
        print(result3)
        print()

    finally:
        session.close()

if __name__ == "__main__":
    test_contact_recommendations()