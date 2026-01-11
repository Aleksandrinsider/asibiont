"""
Расширенный тест оптимизированного промпта
Более детальный анализ с 10+ сценариями
"""
import asyncio
from ai_integration import chat_with_ai
from models import SessionLocal, User, Task
from datetime import datetime, timedelta
import pytz

# Расширенные тестовые сценарии
extended_test_cases = [
    {
        "category": "Диалог",
        "name": "Приветствие",
        "message": "привет, как дела?",
        "checks": ["вопрос", "задач", "профил"],
        "weight": 1.0
    },
    {
        "category": "Диалог",
        "name": "Запрос совета",
        "message": "что посоветуешь?",
        "checks": ["совет", "рекоменд", "предлаг", "?"],
        "weight": 1.5
    },
    {
        "category": "Диалог",
        "name": "Эмоция - усталость",
        "message": "устал",
        "checks": ["понима", "поддерж", "отдох", "?"],
        "weight": 1.5
    },
    {
        "category": "Диалог",
        "name": "Эмоция - стресс",
        "message": "много стресса от работы",
        "checks": ["стресс", "помог", "план", "?"],
        "weight": 1.5
    },
    {
        "category": "Диалог",
        "name": "Эмоция - радость",
        "message": "сегодня отличный день",
        "checks": ["поздрав", "продолж", "что-то", "?"],
        "weight": 1.0
    },
    {
        "category": "Задачи",
        "name": "Добавление простой задачи",
        "message": "добавь задачу купить молоко",
        "checks": ["добавил", "задач", "?"],
        "weight": 1.0
    },
    {
        "category": "Задачи",
        "name": "Добавление задачи со временем",
        "message": "напомни позвонить клиенту завтра в 15:00",
        "checks": ["добавил", "15:00", "завтра", "?"],
        "weight": 1.0
    },
    {
        "category": "Задачи",
        "name": "Добавление срочной задачи",
        "message": "срочно нужно подготовить отчет к 18:00",
        "checks": ["добавил", "18:00", "срочн", "?"],
        "weight": 1.0
    },
    {
        "category": "Задачи",
        "name": "Показ задач",
        "message": "покажи мои задачи",
        "checks": ["задач", "рекоменд", "?"],
        "weight": 1.0
    },
    {
        "category": "Задачи",
        "name": "Показ задач с фильтром",
        "message": "покажи задачи на сегодня",
        "checks": ["сегодня", "задач", "приоритет", "?"],
        "weight": 1.0
    },
    {
        "category": "Задачи",
        "name": "Завершение задачи",
        "message": "выполнил купить молоко",
        "checks": ["отлично", "молод", "?", "дальше"],
        "weight": 1.0
    },
    {
        "category": "Задачи",
        "name": "Завершение с анализом",
        "message": "закончил подготовку презентации",
        "checks": ["отлично", "анализ", "следующ", "?"],
        "weight": 1.0
    },
    {
        "category": "Задачи",
        "name": "Делегирование",
        "message": "@testuser сделай отчет до пятницы",
        "checks": ["делегиров", "testuser", "?"],
        "weight": 1.0
    },
    {
        "category": "Задачи",
        "name": "Делегирование с временем",
        "message": "@testuser проверь код к завтра 12:00",
        "checks": ["делегиров", "12:00", "завтра", "?"],
        "weight": 1.0
    },
    {
        "category": "Профиль",
        "name": "Обновление профиля",
        "message": "живу в москве, работаю в яндексе",
        "checks": ["москв", "яндекс", "?", "найд"],
        "weight": 1.0
    },
    {
        "category": "Профиль",
        "name": "Обновление навыков",
        "message": "умею программировать на python",
        "checks": ["python", "навык", "?", "добав"],
        "weight": 1.0
    },
    {
        "category": "Профиль",
        "name": "Добавление интересов",
        "message": "интересуюсь программированием и AI",
        "checks": ["подтвержд", "добав", "интерес", "?"],
        "weight": 1.5
    },
    {
        "category": "Профиль",
        "name": "Добавление целей",
        "message": "хочу изучить машинное обучение",
        "checks": ["цель", "машинное обучение", "?", "план"],
        "weight": 1.0
    },
    {
        "category": "Люди",
        "name": "Поиск партнеров",
        "message": "найди людей для совместных проектов",
        "checks": ["наш", "людей", "совпад", "?"],
        "weight": 1.0
    },
    {
        "category": "Люди",
        "name": "Поиск по интересам",
        "message": "найди единомышленников по AI",
        "checks": ["AI", "единомышленник", "совпад", "?"],
        "weight": 1.0
    },
    {
        "category": "Edge Cases",
        "name": "Непонятный запрос",
        "message": "сделай это",
        "checks": ["имеешь в виду", "уточ", "?"],
        "weight": 1.5
    },
    {
        "category": "Edge Cases",
        "name": "Бессмысленный ввод",
        "message": "асдфасдф",
        "checks": ["понять", "имеешь в виду", "?"],
        "weight": 1.5
    },
    {
        "category": "Edge Cases",
        "name": "Пустой запрос",
        "message": "",
        "checks": ["помочь", "расскаж", "?"],
        "weight": 1.0
    },
    {
        "category": "Edge Cases",
        "name": "Очень длинный запрос",
        "message": "у меня очень много задач и я не знаю что делать сначала потому что все кажется важным и срочным одновременно и я боюсь что-нибудь забыть или не успеть сделать вовремя",
        "checks": ["приоритет", "план", "помог", "?"],
        "weight": 1.5
    },
]

async def run_extended_test(user_id):
    """Запускает расширенный тест"""
    print("\n" + "="*80)
    print("  РАСШИРЕННЫЙ ТЕСТ ОПТИМИЗИРОВАННОГО ПРОМПТА")
    print("  24 сценария по 5 категориям")
    print("="*80 + "\n")
    
    results = []
    category_stats = {}
    
    for i, test in enumerate(extended_test_cases, 1):
        print(f"[{i}/{len(extended_test_cases)}] {test['category']}: {test['name']}")
        print(f"   Запрос: {test['message']}")
        
        try:
            response = await chat_with_ai(
                message=test['message'],
                context=[],
                user_id=user_id
            )
            
            # Анализ ответа
            word_count = len(response.split())
            sentence_count = response.count('.') + response.count('?') + response.count('!')
            question_marks = response.count('?')
            
            # Проверка ключевых слов
            checks_passed = sum(1 for keyword in test['checks'] if keyword.lower() in response.lower())
            quality_score = (checks_passed / len(test['checks'])) * 100
            
            # Взвешенный score
            weighted_score = quality_score * test['weight']
            
            print(f"   Ответ ({word_count} слов, {sentence_count} предл, {question_marks} ?):")
            print(f"   Качество: {quality_score:.0f}% (взвешенный: {weighted_score:.1f})")
            print(f"   {response[:120]}{'...' if len(response) > 120 else ''}\n")
            
            result = {
                "category": test['category'],
                "test": test['name'],
                "words": word_count,
                "sentences": sentence_count,
                "questions": question_marks,
                "quality": quality_score,
                "weighted_score": weighted_score,
                "weight": test['weight'],
                "response": response
            }
            results.append(result)
            
            # Обновляем статистику по категориям
            if test['category'] not in category_stats:
                category_stats[test['category']] = {
                    "count": 0,
                    "total_quality": 0,
                    "total_words": 0,
                    "total_questions": 0
                }
            
            cat = category_stats[test['category']]
            cat['count'] += 1
            cat['total_quality'] += quality_score
            cat['total_words'] += word_count
            cat['total_questions'] += question_marks
            
            await asyncio.sleep(1.5)
            
        except Exception as e:
            print(f"   ❌ ОШИБКА: {e}\n")
            results.append({
                "category": test['category'],
                "test": test['name'],
                "error": str(e)
            })
    
    # Анализ результатов
    print("\n" + "="*80)
    print("  РЕЗУЛЬТАТЫ ПО КАТЕГОРИЯМ")
    print("="*80 + "\n")
    
    print(f"{'Категория':<20} {'Тестов':<10} {'Качество':<15} {'Слов':<10} {'Вопросов'}")
    print("-" * 80)
    
    for category, stats in category_stats.items():
        avg_quality = stats['total_quality'] / stats['count']
        avg_words = stats['total_words'] / stats['count']
        avg_questions = stats['total_questions'] / stats['count']
        
        print(f"{category:<20} {stats['count']:<10} {avg_quality:.1f}%{'':<10} {avg_words:.0f}{'':<7} {avg_questions:.1f}")
    
    # Общие метрики
    print("\n" + "="*80)
    print("  ОБЩИЕ МЕТРИКИ")
    print("="*80 + "\n")
    
    total_words = sum(r.get('words', 0) for r in results)
    total_quality = sum(r.get('quality', 0) for r in results)
    total_weighted = sum(r.get('weighted_score', 0) for r in results)
    total_questions = sum(r.get('questions', 0) for r in results)
    total_weight = sum(r.get('weight', 0) for r in results)
    
    valid_results = [r for r in results if 'error' not in r]
    if valid_results:
        avg_words = total_words / len(valid_results)
        avg_quality = total_quality / len(valid_results)
        avg_weighted = total_weighted / total_weight
        avg_questions = total_questions / len(valid_results)
        avg_sentences = sum(r.get('sentences', 0) for r in valid_results) / len(valid_results)
        
        print(f"📊 Средние значения:")
        print(f"   Длина ответа:        {avg_words:.1f} слов")
        print(f"   Предложений:         {avg_sentences:.1f}")
        print(f"   Вопросов:            {avg_questions:.1f}")
        print(f"   Качество:            {avg_quality:.1f}%")
        print(f"   Взвешенное качество: {avg_weighted:.1f}%")
        
        # Оценка вовлечения
        engagement_score = (avg_questions / avg_sentences * 100) if avg_sentences > 0 else 0
        print(f"   Вовлечение:          {engagement_score:.1f}% (вопросов от предложений)")
        
        # Детальный анализ
        print(f"\n💡 АНАЛИЗ:")
        
        if avg_words >= 30 and avg_words <= 60:
            print(f"   ✅ Длина ответов оптимальна ({avg_words:.0f} слов)")
        elif avg_words < 30:
            print(f"   ⚠️  Ответы слишком короткие ({avg_words:.0f} слов)")
        else:
            print(f"   ⚠️  Ответы длинноваты ({avg_words:.0f} слов), но не критично")
        
        if avg_questions >= 0.8:
            print(f"   ✅ Хорошая вовлеченность (в среднем {avg_questions:.1f} вопросов)")
        else:
            print(f"   ⚠️  Мало вопросов ({avg_questions:.1f}), нужно больше вовлекать")
        
        if avg_weighted >= 70:
            print(f"   ✅ Отличное качество ответов ({avg_weighted:.0f}%)")
        elif avg_weighted >= 50:
            print(f"   ✅ Хорошее качество ответов ({avg_weighted:.0f}%)")
        else:
            print(f"   ⚠️  Качество требует улучшения ({avg_weighted:.0f}%)")
        
        # Проблемные зоны
        print(f"\n🔍 ПРОБЛЕМНЫЕ ЗОНЫ:")
        problem_tests = [r for r in valid_results if r.get('quality', 0) < 50]
        if problem_tests:
            for test in problem_tests:
                print(f"   ⚠️  {test['category']}: {test['test']} - {test['quality']:.0f}%")
        else:
            print(f"   ✅ Проблемных тестов не обнаружено")
        
        # Сильные стороны
        print(f"\n⭐ СИЛЬНЫЕ СТОРОНЫ:")
        strong_tests = [r for r in valid_results if r.get('quality', 0) >= 75]
        if strong_tests:
            for test in strong_tests:
                print(f"   ✅ {test['category']}: {test['test']} - {test['quality']:.0f}%")
        
        # Рекомендации
        print(f"\n🎯 РЕКОМЕНДАЦИИ ПО УЛУЧШЕНИЮ:")
        
        if engagement_score < 30:
            print(f"   1. Добавить больше вопросов в конце ответов")
        
        category_problems = [cat for cat, stats in category_stats.items() 
                           if (stats['total_quality'] / stats['count']) < 60]
        if category_problems:
            print(f"   2. Улучшить обработку категорий: {', '.join(category_problems)}")
        
        if avg_words > 50:
            print(f"   3. Можно еще сократить ответы для большей краткости")
        
        print(f"\n💰 ЭКОНОМИЯ:")
        print(f"   Промпт: ~4000 токенов экономии на каждый запрос")
        print(f"   Это {((4753 - 784) / 4753 * 100):.1f}% меньше токенов")
        print(f"   На 1000 запросов: экономия ~$3-5 в зависимости от модели")
        
        print(f"\n✅ ИТОГОВЫЙ ВЕРДИКТ:")
        if avg_weighted >= 70 and avg_words <= 60:
            print(f"   🎉 ОПТИМИЗИРОВАННЫЙ ПРОМПТ РАБОТАЕТ ОТЛИЧНО!")
            print(f"   Можно использовать в продакшене")
        elif avg_weighted >= 60:
            print(f"   ✅ ХОРОШО, но есть что улучшить")
            print(f"   Можно использовать с мониторингом")
        else:
            print(f"   ⚠️  ТРЕБУЕТСЯ ДОРАБОТКА")
            print(f"   Нужно улучшить проблемные зоны")
    
    print("\n" + "="*80)
    return results

async def main():
    # Находим пользователя
    session = SessionLocal()
    try:
        user = session.query(User).first()
        if not user:
            print("❌ Нет пользователей в базе")
            return
        
        user_id = user.id
        print(f"[CONFIG] Тестовый пользователь: {user.username} (ID: {user_id})")
        
        # Добавляем тестовые задачи если их нет
        task_count = session.query(Task).filter_by(user_id=user_id, status='active').count()
        if task_count < 2:
            # Удаляем старые
            session.query(Task).filter_by(user_id=user_id).delete()
            
            test_tasks = [
                Task(
                    user_id=user_id,
                    title="Изучить новую архитектуру GPT",
                    reminder_time=datetime.now(pytz.UTC) - timedelta(minutes=10),
                    status="active",
                    created_at=datetime.now(pytz.UTC)
                ),
                Task(
                    user_id=user_id,
                    title="Купить молоко",
                    reminder_time=datetime.now(pytz.UTC) + timedelta(hours=3),
                    status="active",
                    created_at=datetime.now(pytz.UTC)
                ),
            ]
            session.add_all(test_tasks)
            session.commit()
            print(f"[CONFIG] Добавлено {len(test_tasks)} тестовых задач")
    finally:
        session.close()
    
    # Запускаем тест
    await run_extended_test(user_id)

if __name__ == "__main__":
    asyncio.run(main())
