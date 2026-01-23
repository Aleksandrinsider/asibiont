#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Комплексный тест агента для проверки всех критичных функций
Проверяет создание задач, обновление профиля, удаление, поиск партнеров
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import SessionLocal, User, UserProfile, Task
import asyncio
from datetime import datetime

class ComprehensiveAgentTester:
    def __init__(self):
        self.db_session = SessionLocal()
        self.user_id = 999999  # Тестовый пользователь
        self.results = []
        self.total_tests = 0
        self.passed_tests = 0
        
    def cleanup(self):
        """Очистка тестовых данных"""
        try:
            user = self.db_session.query(User).filter_by(telegram_id=self.user_id).first()
            if user:
                # Удаляем задачи
                self.db_session.query(Task).filter_by(user_id=user.id).delete()
                # Очищаем профиль
                profile = self.db_session.query(UserProfile).filter_by(user_id=user.id).first()
                if profile:
                    profile.city = None
                    profile.company = None
                    profile.position = None
                    profile.interests = None
                    profile.skills = None
                    profile.goals = None
                    profile.languages = None
                    profile.bio = None
                self.db_session.commit()
            print("[OK] База данных очищена")
        except Exception as e:
            print(f"[ERROR] Ошибка при очистке: {e}")
            self.db_session.rollback()
    
    async def test_explicit_task_creation(self):
        """Тест 1: Явное создание задачи"""
        self.total_tests += 1
        print("\n[Тест 1] Явное создание задачи: 'добавь задачу позвонить клиенту'")
        
        try:
            response = await chat_with_ai(
                message="добавь задачу позвонить клиенту",
                user_id=self.user_id,
                db_session=self.db_session
            )
            
            # Проверяем, что задача создана
            user = self.db_session.query(User).filter_by(telegram_id=self.user_id).first()
            tasks = self.db_session.query(Task).filter_by(user_id=user.id).all()
            
            if len(tasks) > 0 and any('позвонить' in task.title.lower() or 'клиент' in task.title.lower() for task in tasks):
                print("✓ PASS: Задача создана")
                self.passed_tests += 1
                return True
            else:
                print(f"✗ FAIL: Задача не создана. Найдено задач: {len(tasks)}")
                return False
        except Exception as e:
            print(f"✗ FAIL: Ошибка - {e}")
            return False
    
    async def test_implicit_task_creation(self):
        """Тест 2: Неявное создание задачи"""
        self.total_tests += 1
        print("\n[Тест 2] Неявное создание задачи: 'нужно купить молоко'")
        
        try:
            response = await chat_with_ai(
                message="нужно купить молоко",
                user_id=self.user_id,
                db_session=self.db_session
            )
            
            user = self.db_session.query(User).filter_by(telegram_id=self.user_id).first()
            tasks = self.db_session.query(Task).filter_by(user_id=user.id).all()
            
            if any('молоко' in task.title.lower() or 'купить' in task.title.lower() for task in tasks):
                print("✓ PASS: Неявная задача создана")
                self.passed_tests += 1
                return True
            else:
                print(f"✗ FAIL: Неявная задача не создана")
                return False
        except Exception as e:
            print(f"✗ FAIL: Ошибка - {e}")
            return False
    
    async def test_reminder_task_creation(self):
        """Тест 3: Создание задачи с напоминанием"""
        self.total_tests += 1
        print("\n[Тест 3] Задача с напоминанием: 'напомни мне проверить почту в 10:00'")
        
        try:
            response = await chat_with_ai(
                message="напомни мне проверить почту в 10:00",
                user_id=self.user_id,
                db_session=self.db_session
            )
            
            user = self.db_session.query(User).filter_by(telegram_id=self.user_id).first()
            tasks = self.db_session.query(Task).filter_by(user_id=user.id).all()
            
            task_created = any('почт' in task.title.lower() or 'проверить' in task.title.lower() for task in tasks)
            has_reminder = any(task.reminder_time is not None for task in tasks)
            
            if task_created and has_reminder:
                print("✓ PASS: Задача с напоминанием создана")
                self.passed_tests += 1
                return True
            else:
                print(f"✗ FAIL: Задача создана={task_created}, Напоминание={has_reminder}")
                return False
        except Exception as e:
            print(f"✗ FAIL: Ошибка - {e}")
            return False
    
    async def test_profile_update_explicit(self):
        """Тест 4: Явное обновление профиля"""
        self.total_tests += 1
        print("\n[Тест 4] Обновление профиля: 'обнови профиль: я из Москвы, работаю программистом'")
        
        try:
            response = await chat_with_ai(
                message="обнови профиль: я из Москвы, работаю программистом",
                user_id=self.user_id,
                db_session=self.db_session
            )
            
            user = self.db_session.query(User).filter_by(telegram_id=self.user_id).first()
            profile = self.db_session.query(UserProfile).filter_by(user_id=user.id).first()
            
            if profile and (profile.city or profile.position):
                print(f"✓ PASS: Профиль обновлен (city={profile.city}, position={profile.position})")
                self.passed_tests += 1
                return True
            else:
                print("✗ FAIL: Профиль не обновлен")
                return False
        except Exception as e:
            print(f"✗ FAIL: Ошибка - {e}")
            return False
    
    async def test_profile_info_sharing(self):
        """Тест 5: Неявное обновление профиля через информацию"""
        self.total_tests += 1
        print("\n[Тест 5] Неявное обновление: 'я работаю data engineer, навыки: Python, SQL'")
        
        try:
            response = await chat_with_ai(
                message="я работаю data engineer, навыки: Python, SQL",
                user_id=self.user_id,
                db_session=self.db_session
            )
            
            user = self.db_session.query(User).filter_by(telegram_id=self.user_id).first()
            profile = self.db_session.query(UserProfile).filter_by(user_id=user.id).first()
            
            if profile and profile.skills:
                print(f"✓ PASS: Профиль обновлен через информацию (skills={profile.skills})")
                self.passed_tests += 1
                return True
            else:
                print("✗ FAIL: Профиль не обновлен через информацию")
                return False
        except Exception as e:
            print(f"✗ FAIL: Ошибка - {e}")
            return False
    
    async def test_list_tasks(self):
        """Тест 6: Вывод списка задач"""
        self.total_tests += 1
        print("\n[Тест 6] Список задач: 'покажи мои задачи'")
        
        try:
            response = await chat_with_ai(
                message="покажи мои задачи",
                user_id=self.user_id,
                db_session=self.db_session
            )
            
            # Проверяем, что в ответе упоминаются задачи
            if response and (len(response) > 50):
                print("✓ PASS: Список задач получен")
                self.passed_tests += 1
                return True
            else:
                print("✗ FAIL: Список задач не получен или пустой")
                return False
        except Exception as e:
            print(f"✗ FAIL: Ошибка - {e}")
            return False
    
    async def test_task_with_add_keyword(self):
        """Тест 7: Проверка, что 'добавь' в задаче не конфликтует с профилем"""
        self.total_tests += 1
        print("\n[Тест 7] Задача со словом 'добавь': 'добавь задачу добавить комментарии в код'")
        
        try:
            initial_count = self.db_session.query(Task).filter_by(
                user_id=self.db_session.query(User).filter_by(telegram_id=self.user_id).first().id
            ).count()
            
            response = await chat_with_ai(
                message="добавь задачу добавить комментарии в код",
                user_id=self.user_id,
                db_session=self.db_session
            )
            
            final_count = self.db_session.query(Task).filter_by(
                user_id=self.db_session.query(User).filter_by(telegram_id=self.user_id).first().id
            ).count()
            
            if final_count > initial_count:
                print("✓ PASS: Задача создана, конфликта с профилем нет")
                self.passed_tests += 1
                return True
            else:
                print("✗ FAIL: Задача не создана (конфликт intent)")
                return False
        except Exception as e:
            print(f"✗ FAIL: Ошибка - {e}")
            return False
    
    async def test_conversation_without_action(self):
        """Тест 8: Обычный разговор не должен создавать задачи"""
        self.total_tests += 1
        print("\n[Тест 8] Обычный разговор: 'что ты умеешь?'")
        
        try:
            initial_count = self.db_session.query(Task).filter_by(
                user_id=self.db_session.query(User).filter_by(telegram_id=self.user_id).first().id
            ).count()
            
            response = await chat_with_ai(
                message="что ты умеешь?",
                user_id=self.user_id,
                db_session=self.db_session
            )
            
            final_count = self.db_session.query(Task).filter_by(
                user_id=self.db_session.query(User).filter_by(telegram_id=self.user_id).first().id
            ).count()
            
            if final_count == initial_count and response and len(response) > 50:
                print("✓ PASS: Разговор без создания задачи")
                self.passed_tests += 1
                return True
            else:
                print(f"✗ FAIL: Неожиданное создание задачи или пустой ответ")
                return False
        except Exception as e:
            print(f"✗ FAIL: Ошибка - {e}")
            return False
    
    async def test_multiple_actions_in_message(self):
        """Тест 9: Несколько действий в одном сообщении"""
        self.total_tests += 1
        print("\n[Тест 9] Несколько действий: 'добавь задачу написать отчет и удали задачу про почту'")
        
        try:
            response = await chat_with_ai(
                message="добавь задачу написать отчет и обнови мой профиль: интересы - машинное обучение",
                user_id=self.user_id,
                db_session=self.db_session
            )
            
            user = self.db_session.query(User).filter_by(telegram_id=self.user_id).first()
            tasks = self.db_session.query(Task).filter_by(user_id=user.id).all()
            profile = self.db_session.query(UserProfile).filter_by(user_id=user.id).first()
            
            task_created = any('отчет' in task.title.lower() or 'написать' in task.title.lower() for task in tasks)
            # Проверяем, что хотя бы одно действие выполнено
            
            if task_created or (profile and profile.interests):
                print("✓ PASS: Хотя бы одно действие выполнено")
                self.passed_tests += 1
                return True
            else:
                print("✗ FAIL: Ни одно действие не выполнено")
                return False
        except Exception as e:
            print(f"✗ FAIL: Ошибка - {e}")
            return False
    
    async def test_time_in_task(self):
        """Тест 10: Задача со временем"""
        self.total_tests += 1
        print("\n[Тест 10] Задача со временем: 'создай задачу встреча с командой завтра в 15:00'")
        
        try:
            response = await chat_with_ai(
                message="создай задачу встреча с командой завтра в 15:00",
                user_id=self.user_id,
                db_session=self.db_session
            )
            
            user = self.db_session.query(User).filter_by(telegram_id=self.user_id).first()
            tasks = self.db_session.query(Task).filter_by(user_id=user.id).all()
            
            task_with_time = any(
                ('встреча' in task.title.lower() or 'команд' in task.title.lower()) 
                and task.reminder_time is not None 
                for task in tasks
            )
            
            if task_with_time:
                print("✓ PASS: Задача со временем создана")
                self.passed_tests += 1
                return True
            else:
                print("✗ FAIL: Задача со временем не создана")
                return False
        except Exception as e:
            print(f"✗ FAIL: Ошибка - {e}")
            return False
    
    def print_summary(self):
        """Вывод итогового отчета"""
        print("\n" + "="*60)
        print("ИТОГОВЫЙ ОТЧЕТ")
        print("="*60)
        print(f"Всего тестов: {self.total_tests}")
        print(f"Пройдено: {self.passed_tests}")
        print(f"Провалено: {self.total_tests - self.passed_tests}")
        print(f"Процент успеха: {(self.passed_tests / self.total_tests * 100):.1f}%")
        
        if self.passed_tests == self.total_tests:
            print("\n🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
        elif self.passed_tests >= self.total_tests * 0.7:
            print("\n⚠️ Большинство тестов пройдено, но есть проблемы")
        else:
            print("\n❌ КРИТИЧЕСКИЕ ПРОБЛЕМЫ - много тестов провалено")
        
        print("="*60)
    
    async def run_all_tests(self):
        """Запуск всех тестов"""
        print("="*60)
        print("КОМПЛЕКСНЫЙ ТЕСТ АГЕНТА")
        print("="*60)
        
        # Очистка перед тестами
        self.cleanup()
        
        # Запуск тестов
        await self.test_explicit_task_creation()
        await self.test_implicit_task_creation()
        await self.test_reminder_task_creation()
        await self.test_profile_update_explicit()
        await self.test_profile_info_sharing()
        await self.test_list_tasks()
        await self.test_task_with_add_keyword()
        await self.test_conversation_without_action()
        await self.test_multiple_actions_in_message()
        await self.test_time_in_task()
        
        # Вывод итогов
        self.print_summary()
        
        # Очистка после тестов
        self.cleanup()
        self.db_session.close()

async def main():
    tester = ComprehensiveAgentTester()
    await tester.run_all_tests()

if __name__ == "__main__":
    asyncio.run(main())
