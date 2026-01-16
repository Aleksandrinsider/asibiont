#!/usr/bin/env python3
"""
Автоматический тест для проверки возможностей агента.
Проверяет что агент умеет добавлять, изменять и удалять данные.
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from models import Session, User, UserProfile, Task
from ai_integration.handlers import (
    update_profile, 
    add_task, 
    delete_task, 
    complete_task,
    delete_all_tasks
)
from datetime import datetime, timedelta

# Тестовый пользователь
TEST_USER_ID = 999999999

def print_test(name, passed):
    """Вывод результата теста"""
    status = "✅ PASSED" if passed else "❌ FAILED"
    print(f"{status} - {name}")
    return passed

def cleanup_test_user():
    """Очистка тестового пользователя"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=TEST_USER_ID).first()
        if user:
            # Удалить все связанные данные в правильном порядке
            from models import Interaction
            session.query(Interaction).filter_by(user_id=user.id).delete()
            session.query(Task).filter_by(user_id=user.id).delete()
            session.query(UserProfile).filter_by(user_id=user.id).delete()
            session.delete(user)
            session.commit()
            print("🧹 Тестовый пользователь очищен")
    except Exception as e:
        print(f"⚠️ Ошибка при очистке: {e}")
        session.rollback()
    finally:
        session.close()

def create_test_user():
    """Создание тестового пользователя"""
    session = Session()
    try:
        # Сначала проверим, существует ли пользователь
        user = session.query(User).filter_by(telegram_id=TEST_USER_ID).first()
        if user:
            print("👤 Тестовый пользователь уже существует")
            return user.id
            
        user = User(telegram_id=TEST_USER_ID, username="test_agent_user")
        session.add(user)
        session.commit()
        
        profile = UserProfile(user_id=user.id)
        session.add(profile)
        session.commit()
        
        print("👤 Тестовый пользователь создан")
        return user.id
    finally:
        session.close()

def test_profile_operations():
    """Тест операций с профилем"""
    print("\n📝 ТЕСТИРОВАНИЕ ПРОФИЛЯ")
    print("=" * 60)
    
    results = []
    session = Session()
    
    try:
        # Тест 1: Добавление навыков
        result = update_profile(skills="Python, JavaScript", user_id=TEST_USER_ID)
        profile = session.query(UserProfile).join(User).filter(User.telegram_id == TEST_USER_ID).first()
        passed = profile and "Python" in (profile.skills or "")
        results.append(print_test("Добавление навыков", passed))
        
        # Тест 2: Добавление интересов
        result = update_profile(interests="тестирование, программирование", user_id=TEST_USER_ID)
        session.expire_all()  # Обновить данные из БД
        profile = session.query(UserProfile).join(User).filter(User.telegram_id == TEST_USER_ID).first()
        passed = profile and "тестирование" in (profile.interests or "")
        results.append(print_test("Добавление интересов", passed))
        
        # Тест 3: Изменение города
        result = update_profile(city="Москва", user_id=TEST_USER_ID)
        session.expire_all()
        profile = session.query(UserProfile).join(User).filter(User.telegram_id == TEST_USER_ID).first()
        passed = profile and profile.city == "Москва"
        results.append(print_test("Изменение города", passed))
        
        # Тест 4: Изменение компании и должности
        result = update_profile(company="TestCorp", position="Senior Tester", user_id=TEST_USER_ID)
        session.expire_all()
        profile = session.query(UserProfile).join(User).filter(User.telegram_id == TEST_USER_ID).first()
        passed = profile and profile.company == "TestCorp" and profile.position == "Senior Tester"
        results.append(print_test("Изменение компании и должности", passed))
        
        # Тест 5: Удаление из списка (интересы)
        result = update_profile(interests="-тестирование", user_id=TEST_USER_ID)
        session.expire_all()
        profile = session.query(UserProfile).join(User).filter(User.telegram_id == TEST_USER_ID).first()
        passed = profile and "тестирование" not in (profile.interests or "")
        results.append(print_test("Удаление из списка интересов", passed))
        
        # Тест 6: Очистка поля
        result = update_profile(goals="", user_id=TEST_USER_ID)
        session.expire_all()
        profile = session.query(UserProfile).join(User).filter(User.telegram_id == TEST_USER_ID).first()
        passed = profile and not profile.goals
        results.append(print_test("Очистка поля goals", passed))
        
    finally:
        session.close()
    
    return all(results)

def test_task_operations():
    """Тест операций с задачами"""
    print("\n📋 ТЕСТИРОВАНИЕ ЗАДАЧ")
    print("=" * 60)
    
    results = []
    session = Session()
    
    try:
        user = session.query(User).filter_by(telegram_id=TEST_USER_ID).first()
        
        # Тест 1: Создание задачи
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d 10:00")
        result = add_task(
            title="Тестовая задача 1",
            description="Описание тестовой задачи",
            reminder_time=tomorrow,
            user_id=TEST_USER_ID
        )
        session.expire_all()
        task = session.query(Task).filter_by(user_id=user.id, title="Тестовая задача 1").first()
        passed = task is not None
        results.append(print_test("Создание задачи", passed))
        task1_id = task.id if task else None
        
        # Тест 2: Создание второй задачи
        result = add_task(
            title="Тестовая задача 2",
            reminder_time=tomorrow,
            user_id=TEST_USER_ID
        )
        session.expire_all()
        task = session.query(Task).filter_by(user_id=user.id, title="Тестовая задача 2").first()
        passed = task is not None
        results.append(print_test("Создание второй задачи", passed))
        task2_id = task.id if task else None
        
        # Тест 3: Завершение задачи по названию
        result = complete_task(task_title="Тестовая задача 1", user_id=TEST_USER_ID)
        session.expire_all()
        task = session.query(Task).filter_by(id=task1_id).first()
        passed = task and task.status == "completed"
        results.append(print_test("Завершение задачи по названию", passed))
        
        # Тест 4: Удаление задачи по ID
        if task2_id:
            result = delete_task(task_id=task2_id, user_id=TEST_USER_ID)
            session.expire_all()
            task = session.query(Task).filter_by(id=task2_id).first()
            passed = task is None
            results.append(print_test("Удаление задачи по ID", passed))
        
        # Тест 5: Создание задач для массового удаления
        for i in range(3, 6):
            add_task(
                title=f"Тестовая задача {i}",
                reminder_time=tomorrow,
                user_id=TEST_USER_ID
            )
        
        session.expire_all()
        count_before = session.query(Task).filter_by(user_id=user.id).count()
        
        # Тест 6: Удаление всех задач
        result = delete_all_tasks(user_id=TEST_USER_ID)
        session.expire_all()
        count_after = session.query(Task).filter_by(user_id=user.id).count()
        passed = count_before > 0 and count_after == 0
        results.append(print_test("Удаление всех задач", passed))
        
    finally:
        session.close()
    
    return all(results)

def test_data_persistence():
    """Тест сохранения данных после перезагрузки сессии"""
    print("\n💾 ТЕСТИРОВАНИЕ СОХРАННОСТИ ДАННЫХ")
    print("=" * 60)
    
    results = []
    
    # Записываем данные
    session1 = Session()
    try:
        update_profile(
            skills="Test Skill 1, Test Skill 2",
            city="Test City",
            company="Test Company",
            user_id=TEST_USER_ID
        )
        session1.commit()
    finally:
        session1.close()
    
    # Читаем в новой сессии
    session2 = Session()
    try:
        profile = session2.query(UserProfile).join(User).filter(User.telegram_id == TEST_USER_ID).first()
        
        passed = (
            profile and
            "Test Skill 1" in (profile.skills or "") and
            profile.city == "Test City" and
            profile.company == "Test Company"
        )
        results.append(print_test("Сохранность данных профиля после перезагрузки", passed))
        
    finally:
        session2.close()
    
    return all(results)

def main():
    """Главная функция тестирования"""
    print("🚀 АВТОМАТИЧЕСКОЕ ТЕСТИРОВАНИЕ ВОЗМОЖНОСТЕЙ АГЕНТА")
    print("=" * 60)
    print("Проверка: добавление, изменение и удаление данных")
    print("=" * 60)
    
    # Подготовка
    cleanup_test_user()
    create_test_user()
    
    # Запуск тестов
    all_passed = True
    
    try:
        profile_passed = test_profile_operations()
        task_passed = test_task_operations()
        persistence_passed = test_data_persistence()
        
        all_passed = profile_passed and task_passed and persistence_passed
        
    finally:
        # Очистка
        cleanup_test_user()
    
    # Итоги
    print("\n" + "=" * 60)
    if all_passed:
        print("✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
    else:
        print("❌ НЕКОТОРЫЕ ТЕСТЫ НЕ ПРОШЛИ")
    print("=" * 60)
    
    return 0 if all_passed else 1

if __name__ == '__main__':
    exit(main())
