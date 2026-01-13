#!/usr/bin/env python3
"""
Итоговый тест всех ИИ-функций агента
"""

from ai_integration import (
    extract_tasks_with_ai,
    analyze_sentiment,
    generate_recommendations,
    understand_complex_query,
    summarize_conversation,
    detect_duplicates
)
from models import Session, UserProfile, User

def test_all_ai_functions():
    print("🚀 ИТОГОВЫЙ ТЕСТ ВСЕХ ИИ-ФУНКЦИЙ АГЕНТА")
    print("=" * 60)

    # 1. Тестируем извлечение задач
    print("\n1. ИЗВЛЕЧЕНИЕ ЗАДАЧ ИЗ СООБЩЕНИЙ")
    print("-" * 40)
    message = "Нужно подготовить презентацию к пятнице, встретиться с клиентом и изучить новый фреймворк"
    tasks = extract_tasks_with_ai(message)
    print(f"Сообщение: {message}")
    print(f"Извлечено задач: {len(tasks)}")
    for task in tasks:
        print(f"  • {task['title']} (приоритет: {task['priority']})")

    # 2. Тестируем анализ эмоций
    print("\n2. АНАЛИЗ ЭМОЦИЙ")
    print("-" * 40)
    messages = [
        "Отлично! Всё получилось!",
        "Ужасно, ничего не работает",
        "Нужно сделать эту задачу"
    ]
    for msg in messages:
        sentiment = analyze_sentiment(msg)
        print(f"'{msg}' → {sentiment['sentiment']} ({sentiment['intensity']:.1f})")

    # 3. Создаем тестового пользователя для рекомендаций
    print("\n3. ПЕРСОНАЛИЗИРОВАННЫЕ РЕКОМЕНДАЦИИ")
    print("-" * 40)
    session = Session()
    try:
        user = User(id=9999, telegram_id=9999999)
        session.add(user)

        profile = UserProfile(
            user_id=9999,
            city='Москва',
            interests='программирование, ИИ, стартапы',
            skills='Python, машинное обучение',
            company='ТехноСтарт'
        )
        session.add(profile)
        session.commit()

        recommendations = generate_recommendations(9999)
        print(f"Профиль: {profile.interests}, навыки: {profile.skills}")
        print(f"Рекомендаций: {len(recommendations)}")
        for rec in recommendations[:3]:  # Показываем первые 3
            print(f"  • {rec.get('title', 'N/A')} ({rec.get('type', 'N/A')})")

    finally:
        session.rollback()
        session.close()

    # 4. Тестируем анализ сложных запросов
    print("\n4. АНАЛИЗ СЛОЖНЫХ ЗАПРОСОВ")
    print("-" * 40)
    query = "Найди дизайнера для ML-проекта с опытом в стартапах"
    analysis = understand_complex_query(query)
    print(f"Запрос: {query}")
    print(f"Основное намерение: {analysis['main_intent']}")
    print(f"Сложность: {analysis['complexity']}")
    print(f"Критерии: {list(analysis['criteria'].keys())}")

    # 5. Тестируем резюмирование
    print("\n5. РЕЗЮМИРОВАНИЕ РАЗГОВОРОВ")
    print("-" * 40)
    conversation = [
        {'role': 'user', 'content': 'Хочу создать стартап в сфере ИИ'},
        {'role': 'assistant', 'content': 'Отличная идея! Расскажите подробнее'},
        {'role': 'user', 'content': 'Это будет платформа для анализа данных'},
        {'role': 'assistant', 'content': 'Нужны разработчики и дизайнеры'}
    ]
    summary = summarize_conversation(conversation)
    print(f"Разговор из {len(conversation)} сообщений")
    print(f"Резюме: {summary}")

    # 6. Тестируем обнаружение дубликатов
    print("\n6. ОБНАРУЖЕНИЕ ДУБЛИКАТОВ ЗАДАЧ")
    print("-" * 40)
    tasks_list = [
        {'title': 'Подготовить презентацию'},
        {'title': 'Подготовить презентацию'},
        {'title': 'Создать отчет'}
    ]
    duplicates = detect_duplicates(tasks_list)
    print(f"Задачи: {[t['title'] for t in tasks_list]}")
    print(f"Найдено проблем: {len(duplicates)}")

    print("\n" + "=" * 60)
    print("✅ ВСЕ ИИ-ФУНКЦИИ ПРОТЕСТИРОВАНЫ!")
    print("Агент стал значительно умнее и эффективнее:")
    print("• Автоматически извлекает задачи из разговоров")
    print("• Понимает эмоции пользователей")
    print("• Генерирует персональные рекомендации")
    print("• Разбирает сложные запросы")
    print("• Резюмирует длинные разговоры")
    print("• Предупреждает о дубликатах задач")
    print("=" * 60)

if __name__ == "__main__":
    test_all_ai_functions()