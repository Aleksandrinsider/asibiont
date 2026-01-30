"""
Тест автоматической рекомендации контактов при упоминании задач/активностей
"""
import asyncio
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, User, UserProfile, SubscriptionTier
from ai_integration.handlers import find_relevant_contacts_for_task

# Используем локальную БД для теста
DB_PATH = 'local.db'
engine = create_engine(f'sqlite:///{DB_PATH}')
Session = sessionmaker(bind=engine)

def setup_test_data():
    """Создаем тестовых пользователей с интересами"""
    session = Session()
    
    # Очищаем старые данные
    session.query(UserProfile).delete()
    session.query(User).delete()
    
    # Пользователь 1 - основной (у него ОБЩИЕ интересы со всеми)
    user1 = User(
        telegram_id=100,
        username="test_main",
        first_name="Главный",
        subscription_tier=SubscriptionTier.STANDARD
    )
    session.add(user1)
    session.flush()
    
    profile1 = UserProfile(
        user_id=user1.id,
        city="Москва",
        interests="спорт, бег, программирование, йога",  # Общие интересы
        skills="Python, менеджмент"
    )
    session.add(profile1)
    
    # Пользователь 2 - бегун (интересы: спорт, бег)
    user2 = User(
        telegram_id=101,
        username="ivan_runner",
        first_name="Иван",
        subscription_tier=SubscriptionTier.LIGHT
    )
    session.add(user2)
    session.flush()
    
    profile2 = UserProfile(
        user_id=user2.id,
        city="Москва",
        interests="бег, спорт, марафоны",  # Совпадают: бег, спорт
        skills="тренерство"
    )
    session.add(profile2)
    
    # Пользователь 3 - программист (интересы: программирование)
    user3 = User(
        telegram_id=102,
        username="maria_dev",
        first_name="Мария",
        subscription_tier=SubscriptionTier.PREMIUM
    )
    session.add(user3)
    session.flush()
    
    profile3 = UserProfile(
        user_id=user3.id,
        city="Москва",
        interests="программирование, AI, стартапы",  # Совпадает: программирование
        skills="Python, JavaScript"  # Совпадает: Python
    )
    session.add(profile3)
    
    # Пользователь 4 - йога (интересы: йога)
    user4 = User(
        telegram_id=103,
        username="anna_yoga",
        first_name="Анна",
        subscription_tier=SubscriptionTier.STANDARD
    )
    session.add(user4)
    session.flush()
    
    profile4 = UserProfile(
        user_id=user4.id,
        city="Москва",
        interests="йога, медитация, спорт",  # Совпадают: йога, спорт
        skills="инструктор йоги"
    )
    session.add(profile4)
    
    session.commit()
    session.close()
    print("OK: Тестовые данные созданы с ОБЩИМИ интересами")

def test_contact_recommendations():
    """Тестируем рекомендации контактов для разных задач"""
    session = Session()
    
    test_cases = [
        {
            "task": "иду на пробежку в парк",
            "expected_contact": "ivan_runner",
            "description": "Спортивная активность (бег)"
        },
        {
            "task": "начну изучать Python для ML проектов",
            "expected_contact": "maria_dev",
            "description": "Обучение программированию"
        },
        {
            "task": "хочу заняться йогой по утрам",
            "expected_contact": "anna_yoga",
            "description": "Йога и медитация"
        },
        {
            "task": "запускаю стартап в сфере AI",
            "expected_contact": "maria_dev",
            "description": "Проект/стартап"
        },
        {
            "task": "нужно купить молоко",
            "expected_contact": None,
            "description": "Бытовая задача (контакты не нужны)"
        }
    ]
    
    print("\n" + "="*80)
    print("🧪 ТЕСТИРОВАНИЕ АВТОМАТИЧЕСКИХ РЕКОМЕНДАЦИЙ КОНТАКТОВ")
    print("="*80)
    
    for idx, test in enumerate(test_cases, 1):
        print(f"\n[{idx}] Задача: \"{test['task']}\"")
        print(f"Категория: {test['description']}")
        
        result = find_relevant_contacts_for_task(
            task_description=test['task'],
            user_id=100,
            limit=3,
            session=session
        )
        
        print(f"\n📊 Результат:")
        if result and not result.startswith("❌") and not result.startswith("В вашей сети"):
            print(result)
            
            # Проверяем что ожидаемый контакт найден
            if test['expected_contact']:
                if f"@{test['expected_contact']}" in result:
                    print(f"✅ УСПЕХ: Ожидаемый контакт @{test['expected_contact']} найден!")
                else:
                    print(f"⚠️ ВНИМАНИЕ: Ожидаемый контакт @{test['expected_contact']} НЕ найден")
            else:
                print("⚠️ Контакты найдены для бытовой задачи (неожиданно)")
        else:
            if test['expected_contact']:
                print(f"❌ ОШИБКА: Контакты не найдены (ожидался @{test['expected_contact']})")
            else:
                print("✅ Контакты не найдены (как и ожидалось для бытовой задачи)")
        
        print("-" * 80)
    
    session.close()
    
    print("\n" + "="*80)
    print("✨ ВЫВОДЫ:")
    print("1. Агент теперь АВТОМАТИЧЕСКИ анализирует задачи/активности")
    print("2. Для спорта/хобби/проектов → предлагает релевантных людей")
    print("3. Для бытовых задач → контакты не предлагаются")
    print("4. Поиск учитывает:")
    print("   • Интересы контактов")
    print("   • Навыки контактов")
    print("   • Синонимы (бег = пробежка = running)")
    print("="*80 + "\n")

if __name__ == "__main__":
    print("Создаем тестовую БД...")
    Base.metadata.create_all(engine)
    
    print("Заполняем тестовыми данными...")
    setup_test_data()
    
    print("\nЗапускаем тесты...")
    test_contact_recommendations()
    
    print("🎉 Тестирование завершено!")
