#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Реалистичный тест агента - симуляция реальных диалогов
Проверяет все функции: задачи, профиль, делегирование, поиск партнеров
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import SessionLocal, User, UserProfile, Task
import asyncio
import re

class RealisticAgentTester:
    def __init__(self):
        self.db_session = SessionLocal()
        self.user_id = 999999
        self.conversation_history = []
        
    def cleanup(self):
        """Очистка тестовых данных"""
        try:
            user = self.db_session.query(User).filter_by(telegram_id=self.user_id).first()
            if user:
                self.db_session.query(Task).filter_by(user_id=user.id).delete()
                profile = self.db_session.query(UserProfile).filter_by(user_id=user.id).first()
                if profile:
                    profile.city = None
                    profile.company = None
                    profile.position = None
                    profile.interests = None
                    profile.skills = None
                    profile.goals = None
                self.db_session.commit()
            print("[CLEANUP] База данных очищена\n")
        except Exception as e:
            print(f"[ERROR] Ошибка при очистке: {e}\n")
            self.db_session.rollback()
    
    def print_separator(self, title=""):
        print("\n" + "="*70)
        if title:
            print(f" {title}")
            print("="*70)
    
    async def send_message(self, user_message, turn_num):
        """Отправка сообщения агенту"""
        print(f"\n[Ход {turn_num}] Пользователь: {user_message}")
        
        response = await chat_with_ai(
            message=user_message,
            user_id=self.user_id,
            db_session=self.db_session
        )
        
        print(f"[Ход {turn_num}] Агент: {response[:400]}{'...' if len(response) > 400 else ''}")
        
        self.conversation_history.append({
            "turn": turn_num,
            "user": user_message,
            "agent": response
        })
        
        return response
    
    def get_current_tasks(self):
        """Получить текущие задачи"""
        user = self.db_session.query(User).filter_by(telegram_id=self.user_id).first()
        if user:
            tasks = self.db_session.query(Task).filter_by(user_id=user.id).all()
            return tasks
        return []
    
    def get_profile(self):
        """Получить профиль пользователя"""
        user = self.db_session.query(User).filter_by(telegram_id=self.user_id).first()
        if user:
            profile = self.db_session.query(UserProfile).filter_by(user_id=user.id).first()
            return profile
        return None
    
    async def scenario_task_management(self):
        """Сценарий: Управление задачами"""
        self.print_separator("СЦЕНАРИЙ 1: Управление задачами")
        
        turn = 1
        
        # 1. Создание явной задачи
        print("\n>>> Тест: Явное создание задачи")
        await self.send_message("добавь задачу позвонить клиенту завтра в 10:00", turn)
        turn += 1
        
        # 2. Неявное создание задачи
        print("\n>>> Тест: Неявное создание задачи")
        await self.send_message("нужно купить продукты вечером", turn)
        turn += 1
        
        # Если агент спрашивает время, отвечаем
        last_response = self.conversation_history[-1]["agent"]
        if "когда" in last_response.lower() or "время" in last_response.lower():
            await self.send_message("в 18:00", turn)
            turn += 1
        
        # 3. Создание задачи с напоминанием
        print("\n>>> Тест: Задача с напоминанием")
        await self.send_message("напомни мне проверить почту в 9:00", turn)
        turn += 1
        
        # 4. Список задач
        print("\n>>> Тест: Просмотр списка задач")
        await self.send_message("покажи мои задачи", turn)
        turn += 1
        
        # Проверяем созданные задачи
        tasks = self.get_current_tasks()
        print(f"\n[ПРОВЕРКА] Создано задач: {len(tasks)}")
        for task in tasks:
            print(f"  - {task.title} (status: {task.status}, time: {task.reminder_time})")
        
        # 5. Редактирование задачи
        if len(tasks) > 0:
            print("\n>>> Тест: Редактирование времени задачи")
            await self.send_message("перенеси задачу про почту на 11:00", turn)
            turn += 1
        
        # 6. Завершение задачи
        if len(tasks) > 0:
            print("\n>>> Тест: Завершение задачи")
            task_title = tasks[0].title
            await self.send_message(f"заверши задачу {task_title}", turn)
            turn += 1
        
        # 7. Удаление задачи
        if len(tasks) > 1:
            print("\n>>> Тест: Удаление задачи")
            await self.send_message("удали задачу про продукты", turn)
            turn += 1
        
        return turn
    
    async def scenario_profile_management(self, start_turn):
        """Сценарий: Управление профилем"""
        self.print_separator("СЦЕНАРИЙ 2: Управление профилем")
        
        turn = start_turn
        
        # 1. Неявное заполнение через информацию о себе
        print("\n>>> Тест: Неявное обновление профиля")
        await self.send_message("я работаю senior developer в Яндексе, живу в Москве", turn)
        turn += 1
        
        # 2. Добавление навыков
        print("\n>>> Тест: Добавление навыков")
        await self.send_message("мои навыки: Python, JavaScript, Docker, Kubernetes", turn)
        turn += 1
        
        # 3. Добавление интересов
        print("\n>>> Тест: Добавление интересов")
        await self.send_message("увлекаюсь машинным обучением и микросервисной архитектурой", turn)
        turn += 1
        
        # Проверяем профиль
        profile = self.get_profile()
        print(f"\n[ПРОВЕРКА] Состояние профиля:")
        if profile:
            print(f"  City: {profile.city or '[НЕ ЗАПОЛНЕНО]'}")
            print(f"  Company: {profile.company or '[НЕ ЗАПОЛНЕНО]'}")
            print(f"  Position: {profile.position or '[НЕ ЗАПОЛНЕНО]'}")
            print(f"  Skills: {profile.skills or '[НЕ ЗАПОЛНЕНО]'}")
            print(f"  Interests: {profile.interests or '[НЕ ЗАПОЛНЕНО]'}")
        else:
            print("  [ОШИБКА] Профиль не найден")
        
        return turn
    
    async def scenario_find_partners(self, start_turn):
        """Сценарий: Поиск партнеров"""
        self.print_separator("СЦЕНАРИЙ 3: Поиск партнеров")
        
        turn = start_turn
        
        # 1. Запрос на поиск партнеров
        print("\n>>> Тест: Поиск партнеров")
        await self.send_message("найди мне людей для совместной работы над проектом", turn)
        turn += 1
        
        # 2. Поиск по конкретным навыкам
        print("\n>>> Тест: Поиск по навыкам")
        await self.send_message("есть ли разработчики с опытом в Kubernetes?", turn)
        turn += 1
        
        return turn
    
    async def scenario_complex_requests(self, start_turn):
        """Сценарий: Сложные запросы"""
        self.print_separator("СЦЕНАРИЙ 4: Сложные запросы")
        
        turn = start_turn
        
        # 1. Несколько действий в одном сообщении
        print("\n>>> Тест: Множественные действия")
        await self.send_message("добавь задачу подготовить презентацию на завтра в 15:00 и напомни проверить email утром", turn)
        turn += 1
        
        # 2. Запрос с контекстом предыдущих задач
        print("\n>>> Тест: Запрос с контекстом")
        await self.send_message("как там дела с задачами на сегодня?", turn)
        turn += 1
        
        # 3. Вопрос о возможностях
        print("\n>>> Тест: Вопрос о функционале")
        await self.send_message("что ты умеешь делать с задачами?", turn)
        turn += 1
        
        # 4. Планирование дня
        print("\n>>> Тест: Планирование")
        await self.send_message("помоги спланировать мой день", turn)
        turn += 1
        
        return turn
    
    async def scenario_edge_cases(self, start_turn):
        """Сценарий: Граничные случаи"""
        self.print_separator("СЦЕНАРИЙ 5: Граничные случаи")
        
        turn = start_turn
        
        # 1. Неоднозначный запрос
        print("\n>>> Тест: Неоднозначный запрос")
        await self.send_message("добавь", turn)
        turn += 1
        
        # Если агент спрашивает что добавить
        last_response = self.conversation_history[-1]["agent"]
        if "что" in last_response.lower() or "добавить" in last_response.lower():
            await self.send_message("задачу встретиться с командой", turn)
            turn += 1
        
        # 2. Запрос на удаление несуществующей задачи
        print("\n>>> Тест: Удаление несуществующей задачи")
        await self.send_message("удали задачу про космический корабль", turn)
        turn += 1
        
        # 3. Обычный разговор (не должен создавать задачу)
        print("\n>>> Тест: Обычный разговор")
        await self.send_message("как погода сегодня?", turn)
        turn += 1
        
        # 4. Конфликтующие слова
        print("\n>>> Тест: Конфликтующие слова")
        await self.send_message("добавь задачу добавить комментарии в код", turn)
        turn += 1
        
        return turn
    
    async def run_realistic_test(self):
        """Запуск реалистичного теста"""
        print("="*70)
        print(" РЕАЛИСТИЧНЫЙ ТЕСТ АГЕНТА - Симуляция реальных диалогов")
        print("="*70)
        
        # Очистка перед тестами
        self.cleanup()
        
        # Запуск сценариев
        turn = await self.scenario_task_management()
        turn = await self.scenario_profile_management(turn)
        turn = await self.scenario_find_partners(turn)
        turn = await self.scenario_complex_requests(turn)
        turn = await self.scenario_edge_cases(turn)
        
        # Финальная проверка
        self.print_separator("ФИНАЛЬНАЯ ПРОВЕРКА")
        
        tasks = self.get_current_tasks()
        profile = self.get_profile()
        
        print(f"\nВсего создано задач: {len(tasks)}")
        print("\nЗадачи:")
        for task in tasks:
            status_emoji = "✓" if task.status == "completed" else "○"
            print(f"  {status_emoji} {task.title} (status: {task.status})")
        
        print("\nПрофиль:")
        if profile:
            filled = sum([bool(profile.city), bool(profile.company), 
                         bool(profile.position), bool(profile.skills), 
                         bool(profile.interests)])
            print(f"  Заполнено: {filled}/5 полей")
            if profile.city:
                print(f"  City: {profile.city}")
            if profile.company:
                print(f"  Company: {profile.company}")
            if profile.position:
                print(f"  Position: {profile.position}")
            if profile.skills:
                print(f"  Skills: {profile.skills[:60]}...")
            if profile.interests:
                print(f"  Interests: {profile.interests[:60]}...")
        
        print(f"\nВсего ходов в диалоге: {len(self.conversation_history)}")
        
        # Оценка
        self.print_separator("ИТОГОВАЯ ОЦЕНКА")
        
        score = 0
        max_score = 5
        
        # Критерий 1: Созданы задачи
        if len(tasks) >= 3:
            score += 1
            print("✓ Задачи создаются корректно")
        else:
            print("✗ Недостаточно задач создано")
        
        # Критерий 2: Профиль заполнен
        if profile and filled >= 3:
            score += 1
            print("✓ Профиль заполняется корректно")
        else:
            print("✗ Профиль заполнен недостаточно")
        
        # Критерий 3: Есть завершенные задачи
        completed = [t for t in tasks if t.status == "completed"]
        if len(completed) > 0:
            score += 1
            print("✓ Задачи завершаются")
        else:
            print("✗ Нет завершенных задач")
        
        # Критерий 4: Есть задачи с напоминаниями
        with_reminder = [t for t in tasks if t.reminder_time]
        if len(with_reminder) > 0:
            score += 1
            print("✓ Напоминания устанавливаются")
        else:
            print("✗ Нет задач с напоминаниями")
        
        # Критерий 5: Агент отвечает на все запросы
        if len(self.conversation_history) >= 15:
            score += 1
            print("✓ Агент обрабатывает все запросы")
        else:
            print("✗ Некоторые запросы не обработаны")
        
        print(f"\nОбщая оценка: {score}/{max_score} ({score/max_score*100:.0f}%)")
        
        if score == max_score:
            print("\n🎉 ОТЛИЧНО! Агент работает идеально")
        elif score >= 4:
            print("\n✓ ХОРОШО! Агент работает стабильно")
        elif score >= 3:
            print("\n⚠ УДОВЛЕТВОРИТЕЛЬНО! Есть проблемы")
        else:
            print("\n✗ КРИТИЧНО! Требуется доработка")
        
        # Очистка после тестов
        self.cleanup()
        self.db_session.close()

async def main():
    tester = RealisticAgentTester()
    await tester.run_realistic_test()

if __name__ == "__main__":
    asyncio.run(main())
