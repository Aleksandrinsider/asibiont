#!/usr/bin/env python3
"""
Тест состояний профиля - проверка поведения AI при разных уровнях заполненности профиля
"""

import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import UserProfile, init_db, get_session
from config import TEST_USER_ID
import logging

logging.getLogger().setLevel(logging.CRITICAL)

class ProfileStateTester:
    def __init__(self):
        self.user_id = TEST_USER_ID

    async def test_profile_states(self):
        """Тестируем разные состояния профиля"""
        print("📋 ТЕСТ СОСТОЯНИЙ ПРОФИЛЯ")
        print("=" * 35)

        await init_db()
        session = get_session()

        # Тестовые состояния профиля
        states = [
            ("Пустой профиль", {
                'city': None, 'interests': None, 'skills': None, 'goals': None,
                'interaction_count': 0
            }),
            ("Частично заполненный", {
                'city': 'Москва', 'interests': 'Python', 'skills': None, 'goals': None,
                'interaction_count': 2
            }),
            ("Почти полный", {
                'city': 'Москва', 'interests': 'Python, AI', 'skills': 'программирование',
                'goals': 'изучить ML', 'interaction_count': 4
            }),
        ]

        for state_name, profile_data in states:
            print(f"\n🔹 {state_name}")

            # Создаем профиль с нужным состоянием
            session.query(UserProfile).filter_by(user_id=self.user_id).delete()
            profile = UserProfile(user_id=self.user_id, **profile_data)
            session.add(profile)
            session.commit()

            # Тестируем приветствие
            try:
                response = await asyncio.wait_for(
                    chat_with_ai(self.user_id, "Привет!", use_cache=False),
                    timeout=8
                )

                # Анализируем ответ
                has_questions = self.analyze_response(response, profile_data)
                status = "✅" if has_questions else "⚪"
                print(f"   {status} Активные вопросы: {'ДА' if has_questions else 'НЕТ'}")

            except Exception as e:
                print(f"   ❌ Ошибка: {str(e)[:25]}...")

        session.close()
        print("\n🎯 Тест завершен!")

    def analyze_response(self, response, profile_data):
        """Анализируем, задает ли AI вопросы в зависимости от состояния профиля"""
        response_lower = response.lower()

        # Проверяем, какие поля пустые
        empty_fields = []
        if not profile_data.get('city'):
            empty_fields.append('город')
        if not profile_data.get('interests'):
            empty_fields.append('интересы')
        if not profile_data.get('skills'):
            empty_fields.append('навыки')
        if not profile_data.get('goals'):
            empty_fields.append('цели')

        # Проверяем, спрашивает ли AI о пустых полях
        asks_about_empty = any(
            field in response_lower for field in empty_fields
        )

        # Проверяем периодические вопросы о целях (каждые 5 взаимодействий)
        interaction_count = profile_data.get('interaction_count', 0)
        should_ask_goals = interaction_count > 0 and interaction_count % 5 == 0
        asks_about_goals = 'цели' in response_lower or 'месяц' in response_lower

        return asks_about_empty or (should_ask_goals and asks_about_goals)

async def main():
    tester = ProfileStateTester()
    await tester.test_profile_states()

if __name__ == "__main__":
    asyncio.run(main())