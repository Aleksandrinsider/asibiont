#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Тестирование новых функций агента:
- analyze_group_opportunities (динамический групповой анализ)
- generate_proactive_context (проактивные предложения)
- Интеграция с реальными задачами
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timedelta
import pytz
from models import Session, User, UserProfile, Task, Base, engine
from ai_integration.handlers import analyze_group_opportunities
from ai_integration.prompts import generate_proactive_context

def create_test_data():
    """Создаем тестовых пользователей и задачи"""
    session = Session()
    
    # Очищаем старые тестовые данные
    session.query(Task).filter(Task.user_id.in_([9001, 9002, 9003, 9004, 9005, 9006, 9007, 9008])).delete(synchronize_session=False)
    session.query(UserProfile).filter(UserProfile.user_id.in_([9001, 9002, 9003, 9004, 9005, 9006, 9007, 9008])).delete(synchronize_session=False)
    session.query(User).filter(User.id.in_([9001, 9002, 9003, 9004, 9005, 9006, 9007, 9008])).delete(synchronize_session=False)
    session.commit()
    
    now = datetime.now(pytz.UTC)
    tomorrow = now + timedelta(days=1)
    
    # Пользователь 1: интересуется стартапами и AI
    user1 = User(
        id=9001,
        telegram_id=9001,
        username='test_alex',
        first_name='Alex',
        timezone='Europe/Moscow'
    )
    session.add(user1)
    session.commit()
    
    profile1 = UserProfile(
        user_id=user1.id,
        interests='стартапы, AI, нейросети',
        goals='запустить AI-стартап',
        skills='Python, Machine Learning',
        city='Москва'
    )
    session.add(profile1)
    
    # Задача 1: AI-проект завтра
    task1 = Task(
        user_id=user1.id,
        title='Созвон по AI-проекту',
        description='Обсудить архитектуру нейросети',
        reminder_time=tomorrow.replace(hour=15, minute=0),
        status='pending'
    )
    session.add(task1)
    
    # Пользователь 2: тоже интересуется стартапами
    user2 = User(
        id=9002,
        telegram_id=9002,
        username='test_maria',
        first_name='Maria',
        timezone='Europe/Moscow'
    )
    session.add(user2)
    session.commit()
    
    profile2 = UserProfile(
        user_id=user2.id,
        interests='бизнес, стартапы, инвестиции',
        goals='запустить стартап',
        skills='Маркетинг, Продажи',
        city='Москва'
    )
    session.add(profile2)
    
    # Задача 2: работа над стартапом
    task2 = Task(
        user_id=user2.id,
        title='Подготовить презентацию для стартапа',
        description='Питч для инвесторов',
        reminder_time=now + timedelta(hours=3),
        status='pending'
    )
    session.add(task2)
    
    # Пользователь 3: программист, работает с AI
    user3 = User(
        id=9003,
        telegram_id=9003,
        username='test_ivan',
        first_name='Ivan',
        timezone='Europe/Moscow'
    )
    session.add(user3)
    session.commit()
    
    profile3 = UserProfile(
        user_id=user3.id,
        interests='программирование, AI, спорт',
        goals='стать ML-инженером',
        skills='Python, TensorFlow',
        city='Москва'
    )
    session.add(profile3)
    
    # Задача 3: пробежка сегодня
    task3 = Task(
        user_id=user3.id,
        title='Пробежка в парке',
        description='5км',
        reminder_time=now.replace(hour=19, minute=0),
        status='pending'
    )
    session.add(task3)
    
    # Задача 4: AI-проект
    task4 = Task(
        user_id=user3.id,
        title='Обучить модель нейросети',
        description='Датасет для компьютерного зрения',
        reminder_time=tomorrow.replace(hour=10, minute=0),
        status='pending'
    )
    session.add(task4)
    
    # Пользователь 4: тоже работает с нейросетями
    user4 = User(
        id=9004,
        telegram_id=9004,
        username='test_sergey',
        first_name='Sergey',
        timezone='Europe/Moscow'
    )
    session.add(user4)
    session.commit()
    
    profile4 = UserProfile(
        user_id=user4.id,
        interests='нейросети, deep learning',
        goals='запустить стартап в AI',
        skills='PyTorch, Computer Vision',
        city='Москва'
    )
    session.add(profile4)
    
    # Задача 5: работа с нейросетями
    task5 = Task(
        user_id=user4.id,
        title='Оптимизировать нейросеть',
        description='Уменьшить время инференса',
        reminder_time=now + timedelta(hours=6),
        status='pending'
    )
    session.add(task5)
    
    # Пользователь 5: основной тестируемый (текущий пользователь)
    user5 = User(
        id=9005,
        telegram_id=9005,
        username='test_denis',
        first_name='Denis',
        timezone='Europe/Moscow'
    )
    session.add(user5)
    session.commit()
    
    profile5 = UserProfile(
        user_id=user5.id,
        interests='AI, нейросети, стартапы, спорт',
        goals='запустить AI-проект',
        skills='Python, ML, нейросети',
        city='Москва'
    )
    session.add(profile5)
    
    # ДОПОЛНИТЕЛЬНЫЕ ПОЛЬЗОВАТЕЛИ для группового анализа
    
    # Пользователь 6: еще один работающий с нейросетями
    user6 = User(
        id=9006,
        telegram_id=9006,
        username='test_oleg',
        first_name='Oleg',
        timezone='Europe/Moscow'
    )
    session.add(user6)
    session.commit()
    
    profile6 = UserProfile(
        user_id=user6.id,
        interests='нейросети, machine learning',
        goals='создать ML-продукт',
        skills='Python, Keras',
        city='Москва'
    )
    session.add(profile6)
    
    task6 = Task(
        user_id=user6.id,
        title='Внедрить нейросеть в продакшн',
        description='Развернуть модель на сервере',
        reminder_time=now + timedelta(hours=12),
        status='pending'
    )
    session.add(task6)
    
    # Пользователь 7: работает со стартапом
    user7 = User(
        id=9007,
        telegram_id=9007,
        username='test_anna',
        first_name='Anna',
        timezone='Europe/Moscow'
    )
    session.add(user7)
    session.commit()
    
    profile7 = UserProfile(
        user_id=user7.id,
        interests='стартапы, бизнес',
        goals='найти инвестиции для стартапа',
        skills='Финансы, Управление',
        city='Москва'
    )
    session.add(profile7)
    
    task7 = Task(
        user_id=user7.id,
        title='Подготовить бизнес-план для стартапа',
        description='Финмодель на 3 года',
        reminder_time=now + timedelta(hours=24),
        status='pending'
    )
    session.add(task7)
    
    # Пользователь 8: еще один с нейросетями (для достижения порога 3+)
    user8 = User(
        id=9008,
        telegram_id=9008,
        username='test_dmitry',
        first_name='Dmitry',
        timezone='Europe/Moscow'
    )
    session.add(user8)
    session.commit()
    
    profile8 = UserProfile(
        user_id=user8.id,
        interests='AI, нейросети',
        goals='внедрить AI в бизнес',
        skills='Python, Deep Learning',
        city='Москва'
    )
    session.add(profile8)
    
    task8 = Task(
        user_id=user8.id,
        title='Тестирование нейросети на реальных данных',
        description='Проверить точность модели',
        reminder_time=now + timedelta(hours=18),
        status='pending'
    )
    session.add(task8)
    
    session.commit()
    session.close()
    
    print("✅ Тестовые данные созданы:")
    print("  - 8 пользователей")
    print("  - 8 задач с разными темами")
    print("  - Общие интересы: AI, стартапы, нейросети, спорт")
    print("  - Группы: 5 человек работают с нейросетями, 4 со стартапами")
    print()


def test_analyze_group_opportunities():
    """Тестируем динамический групповой анализ"""
    print("=" * 60)
    print("ТЕСТ 1: Анализ групповых возможностей")
    print("=" * 60)
    
    session = Session()
    
    # Показываем профиль пользователя 5
    user = session.query(User).filter_by(id=9005).first()
    profile = session.query(UserProfile).filter_by(user_id=9005).first()
    
    print(f"\n👤 Текущий пользователь: @{user.username}")
    print(f"   Интересы: {profile.interests}")
    print(f"   Цели: {profile.goals}")
    print(f"   Навыки: {profile.skills}")
    
    # Тестируем для пользователя 5 (Denis)
    result = analyze_group_opportunities(9005, session)
    
    print("\n📊 Результат analyze_group_opportunities():")
    if result:
        print(f"✅ {result}")
    else:
        print("❌ Групповые возможности не найдены")
        print("\n🔍 Причина: требуется минимум 3 пользователя с общим значимым словом")
        print("   и это слово должно быть релевантно профилю текущего пользователя")
    
    session.close()
    print()


def test_proactive_context():
    """Тестируем проактивный контекст"""
    print("=" * 60)
    print("ТЕСТ 2: Проактивный контекст")
    print("=" * 60)
    
    session = Session()
    
    # Генерируем проактивный контекст для пользователя 5
    context = generate_proactive_context(9005, session)
    
    print("\n📝 Сгенерированный контекст:")
    if context:
        print(context)
    else:
        print("❌ Контекст не сгенерирован")
    
    session.close()
    print()


def test_find_similar_tasks():
    """Проверяем, какие задачи найдутся по ключевым словам"""
    print("=" * 60)
    print("ТЕСТ 3: Поиск похожих задач (детальный)")
    print("=" * 60)
    
    session = Session()
    
    from collections import defaultdict
    
    # Получаем все задачи
    all_tasks = session.query(Task).filter(
        Task.user_id != 9005,
        Task.status == 'pending'
    ).all()
    
    print(f"\n📋 Всего активных задач других пользователей: {len(all_tasks)}")
    
    # Анализируем по словам
    word_to_tasks = defaultdict(list)
    stop_words = {'в', 'на', 'с', 'для', 'по', 'из', 'к', 'о', 'от', 'и', 'а', 'но', 'что', 'как', 'это'}
    
    for task in all_tasks:
        task_text = f"{task.title} {task.description or ''}".lower()
        words = [w.strip('.,!?;:()[]{}') for w in task_text.split()]
        significant_words = [w for w in words if len(w) >= 4 and w not in stop_words]
        
        user = session.query(User).filter_by(id=task.user_id).first()
        
        for word in significant_words:
            word_to_tasks[word].append({
                'username': user.username if user else 'unknown',
                'task': task.title
            })
    
    print("\n🔍 Значимые слова и их частота:")
    for word, tasks in sorted(word_to_tasks.items(), key=lambda x: len(x[1]), reverse=True):
        unique_users = set(t['username'] for t in tasks)
        if len(unique_users) >= 2:
            print(f"  '{word}': {len(unique_users)} пользователей - {', '.join([f'@{u}' for u in list(unique_users)[:3]])}")
    
    session.close()
    print()


def test_partner_tasks():
    """Проверяем задачи партнеров"""
    print("=" * 60)
    print("ТЕСТ 4: Задачи партнеров в ближайшие 48ч")
    print("=" * 60)
    
    session = Session()
    
    from ai_integration.handlers import get_partners_list
    
    # Получаем партнеров для пользователя 5
    partners = get_partners_list(9005, session)
    
    print(f"\n👥 Найдено партнеров: {len(partners)}")
    
    now = datetime.now(pytz.UTC)
    next_48h = now + timedelta(hours=48)
    
    for partner in partners[:5]:
        user = session.query(User).filter_by(id=partner.user_id).first()
        if not user:
            continue
        
        tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status == 'pending',
            Task.reminder_time.isnot(None),
            Task.reminder_time >= now,
            Task.reminder_time <= next_48h
        ).all()
        
        if tasks:
            print(f"\n  @{user.username}:")
            for task in tasks:
                tz = pytz.timezone('Europe/Moscow')
                task_time = task.reminder_time.replace(tzinfo=pytz.UTC).astimezone(tz)
                print(f"    - {task.title} ({task_time.strftime('%d.%m в %H:%M')})")
    
    session.close()
    print()


def run_all_tests():
    """Запускаем все тесты"""
    print("\n" + "=" * 60)
    print("🚀 КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ АГЕНТА")
    print("=" * 60)
    print()
    
    # Создаем тестовые данные
    create_test_data()
    
    # Тест 1: Групповой анализ
    test_analyze_group_opportunities()
    
    # Тест 2: Проактивный контекст
    test_proactive_context()
    
    # Тест 3: Детальный анализ задач
    test_find_similar_tasks()
    
    # Тест 4: Задачи партнеров
    test_partner_tasks()
    
    print("=" * 60)
    print("✅ ВСЕ ТЕСТЫ ЗАВЕРШЕНЫ")
    print("=" * 60)
    print()


if __name__ == "__main__":
    try:
        run_all_tests()
    except Exception as e:
        print(f"\n❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
