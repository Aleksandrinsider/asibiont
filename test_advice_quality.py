"""
Тест качества и действенности советов AI-агента
Проверяет насколько конкретны, практичны и выполнимы рекомендации
"""
import asyncio
import sys
import os
from datetime import datetime, timedelta, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ai_integration.chat import chat_with_ai
from models import Session, User, UserProfile, Base, engine, Task
from reminder_service import ReminderService
import reminder_service as reminder_service_module

# Критерии оценки советов
QUALITY_CRITERIA = {
    'concrete': 'Конкретность (есть точные действия, цифры, инструменты)',
    'actionable': 'Выполнимость (можно сделать прямо сейчас)',
    'specific_tools': 'Конкретные инструменты/ресурсы (названия сервисов, книг, людей)',
    'time_bound': 'Временные рамки (когда делать)',
    'measurable': 'Измеримость (как понять успех)',
    'alternatives': 'Альтернативы (несколько вариантов)',
    'not_generic': 'Не общие фразы ("надо работать", "пробуй разное")'
}

# Тестовые сценарии реальных проблем
TEST_SCENARIOS = [
    {
        'name': 'Привлечение первых пользователей',
        'profile': {
            'interests': 'ИИ, стартапы, SaaS',
            'company': 'AI Task Manager',
            'position': 'Основатель',
            'goals': 'Привлечь первых 10 платящих пользователей'
        },
        'message': 'не могу привлечь первых пользователей. с чего начать?',
        'expected_elements': ['конкретный канал', 'цифры', 'первые шаги', 'где искать']
    },
    {
        'name': 'Низкая продуктивность',
        'profile': {
            'interests': 'программирование, продуктивность',
            'goals': 'Написать 1000 строк кода в неделю'
        },
        'message': 'работаю по 8 часов но мало успеваю. как повысить продуктивность?',
        'expected_elements': ['конкретная техника', 'инструмент', 'время', 'метрика']
    },
    {
        'name': 'Поиск партнеров для стартапа',
        'profile': {
            'interests': 'стартапы, бизнес',
            'company': 'TechStartup',
            'goals': 'Найти технического со-основателя'
        },
        'message': 'ищу технического со-основателя. где искать и как подходить?',
        'expected_elements': ['конкретные площадки', 'как писать', 'критерии отбора']
    },
    {
        'name': 'Монетизация проекта',
        'profile': {
            'interests': 'бизнес, продукты',
            'company': 'FreeApp',
            'goals': 'Заработать первые $1000'
        },
        'message': 'есть 500 активных пользователей. как монетизировать?',
        'expected_elements': ['модель монетизации', 'цена', 'конверсия', 'тестирование']
    },
    {
        'name': 'Обучение новому навыку',
        'profile': {
            'interests': 'программирование',
            'goals': 'Выучить Python за месяц'
        },
        'message': 'хочу выучить python за месяц. реально? с чего начать?',
        'expected_elements': ['курс/ресурс', 'план обучения', 'практика', 'проверка']
    }
]

def analyze_advice_quality(response_text, expected_elements):
    """Анализирует качество совета по критериям"""
    results = {}
    details = []
    
    # 1. Конкретность - есть ли цифры, конкретные действия
    has_numbers = any(char.isdigit() for char in response_text)
    has_concrete_verbs = any(verb in response_text.lower() for verb in [
        'создай', 'напиши', 'позвони', 'опубликуй', 'отправь', 'зарегистрируйся',
        'установи', 'открой', 'скачай', 'подключись', 'запусти'
    ])
    results['concrete'] = has_numbers and has_concrete_verbs
    if results['concrete']:
        details.append("✅ Есть конкретные действия и цифры")
    else:
        details.append("❌ Нет конкретных действий или цифр")
    
    # 2. Конкретные инструменты/ресурсы
    has_specific_names = any(marker in response_text for marker in [
        'http', '.com', '.ru', '@', 'Telegram', 'YouTube', 'GitHub', 
        'Habr', 'VC.ru', 'LinkedIn'
    ])
    results['specific_tools'] = has_specific_names
    if results['specific_tools']:
        details.append("✅ Упомянуты конкретные инструменты/ресурсы")
    else:
        details.append("❌ Нет конкретных инструментов (только общие слова)")
    
    # 3. Не общие фразы
    generic_phrases = [
        'пробуй разное', 'работай над собой', 'нужно стараться',
        'это сложно', 'зависит от ситуации', 'попробуй подумать',
        'может быть', 'возможно'
    ]
    has_generic = any(phrase in response_text.lower() for phrase in generic_phrases)
    results['not_generic'] = not has_generic
    if results['not_generic']:
        details.append("✅ Нет общих фраз")
    else:
        details.append("❌ Есть общие фразы без конкретики")
    
    # 4. Временные рамки
    time_markers = ['сегодня', 'завтра', 'неделю', 'месяц', 'день', 'час', 'минут']
    has_time = any(marker in response_text.lower() for marker in time_markers)
    results['time_bound'] = has_time
    if results['time_bound']:
        details.append("✅ Есть временные рамки")
    else:
        details.append("❌ Нет временных рамок")
    
    # 5. Альтернативы (несколько вариантов)
    has_alternatives = response_text.count('или') >= 1 or any(marker in response_text for marker in ['вариант', 'способ', 'путь'])
    results['alternatives'] = has_alternatives
    if results['alternatives']:
        details.append("✅ Предложены альтернативы")
    else:
        details.append("❌ Только один вариант")
    
    # 6. Измеримость
    measurable_words = ['метрика', 'измер', 'результат', 'KPI', 'цель', 'показатель', 'конверси']
    has_measurable = any(word in response_text.lower() for word in measurable_words)
    results['measurable'] = has_measurable
    if results['measurable']:
        details.append("✅ Есть измеримые результаты")
    else:
        details.append("❌ Не указано как измерить успех")
    
    # 7. Проверка ожидаемых элементов
    expected_found = sum(1 for elem in expected_elements if elem.lower() in response_text.lower())
    results['expected_coverage'] = expected_found / len(expected_elements) if expected_elements else 0
    details.append(f"📊 Покрытие ожидаемых элементов: {expected_found}/{len(expected_elements)}")
    
    # Общий балл
    score = sum(1 for k, v in results.items() if k != 'expected_coverage' and v)
    total = len([k for k in results.keys() if k != 'expected_coverage'])
    
    return {
        'score': score,
        'total': total,
        'percentage': (score / total * 100) if total > 0 else 0,
        'results': results,
        'details': details
    }

async def run_advice_quality_test():
    """Запускает тест качества советов"""
    
    user_id = 999888777
    Base.metadata.create_all(engine)
    
    reminder_svc = ReminderService(bot=None)
    reminder_service_module.REMINDER_SERVICE = reminder_svc
    
    session = Session()
    
    # Очистка
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if user:
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            session.delete(profile)
        session.query(Task).filter_by(user_id=user.id).delete()
        session.commit()
        session.delete(user)
        session.commit()
    
    print("="*80)
    print("[TEST] АНАЛИЗ КАЧЕСТВА И ДЕЙСТВЕННОСТИ СОВЕТОВ")
    print("="*80)
    print()
    
    overall_results = []
    
    for idx, scenario in enumerate(TEST_SCENARIOS, 1):
        print(f"\n{'='*80}")
        print(f"СЦЕНАРИЙ {idx}/{len(TEST_SCENARIOS)}: {scenario['name']}")
        print(f"{'='*80}")
        
        # Создаем пользователя с профилем для сценария
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            if profile:
                session.delete(profile)
            session.query(Task).filter_by(user_id=user.id).delete()
            session.commit()
            session.delete(user)
            session.commit()
        
        user = User(
            telegram_id=user_id,
            username='test_advice',
            first_name='Тест',
            timezone='Europe/Moscow'
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        
        profile_data = scenario['profile']
        profile = UserProfile(
            user_id=user.id,
            interests=profile_data.get('interests', ''),
            goals=profile_data.get('goals', ''),
            company=profile_data.get('company', ''),
            position=profile_data.get('position', ''),
            city='Москва'
        )
        session.add(profile)
        session.commit()
        
        # Отправляем сообщение
        print(f"\n[USER] {scenario['message']}")
        print("\n[AI] Обрабатываю...")
        
        response = await chat_with_ai(
            message=scenario['message'],
            user_id=user_id,
            context="",
            db_session=session,
            message_type='simple'
        )
        
        response_text = response.get('response', '')
        print(f"\n[RESPONSE]\n{response_text}\n")
        
        # Анализируем качество
        print("[АНАЛИЗ КАЧЕСТВА]")
        analysis = analyze_advice_quality(response_text, scenario['expected_elements'])
        
        for detail in analysis['details']:
            print(f"  {detail}")
        
        print(f"\n📊 ОЦЕНКА: {analysis['score']}/{analysis['total']} ({analysis['percentage']:.0f}%)")
        
        if analysis['percentage'] >= 70:
            print("✅ ХОРОШИЙ СОВЕТ - конкретный и действенный")
        elif analysis['percentage'] >= 50:
            print("⚠️ СРЕДНИЙ СОВЕТ - есть конкретика, но можно улучшить")
        else:
            print("❌ СЛАБЫЙ СОВЕТ - слишком общий, мало конкретики")
        
        overall_results.append({
            'scenario': scenario['name'],
            'score': analysis['score'],
            'total': analysis['total'],
            'percentage': analysis['percentage'],
            'results': analysis['results']
        })
    
    # Общая статистика
    print(f"\n\n{'='*80}")
    print("ИТОГОВАЯ СТАТИСТИКА")
    print(f"{'='*80}\n")
    
    avg_score = sum(r['score'] for r in overall_results) / len(overall_results)
    avg_total = sum(r['total'] for r in overall_results) / len(overall_results)
    avg_percentage = sum(r['percentage'] for r in overall_results) / len(overall_results)
    
    print(f"Протестировано сценариев: {len(overall_results)}")
    print(f"Средняя оценка: {avg_score:.1f}/{avg_total:.1f} ({avg_percentage:.1f}%)\n")
    
    # Детальная статистика по критериям
    print("СТАТИСТИКА ПО КРИТЕРИЯМ:")
    criteria_stats = {}
    for criterion in ['concrete', 'specific_tools', 'not_generic', 'time_bound', 'alternatives', 'measurable']:
        passed = sum(1 for r in overall_results if r['results'].get(criterion, False))
        criteria_stats[criterion] = (passed, len(overall_results))
        percentage = (passed / len(overall_results) * 100)
        status = "✅" if percentage >= 70 else "⚠️" if percentage >= 50 else "❌"
        print(f"  {status} {QUALITY_CRITERIA[criterion]}: {passed}/{len(overall_results)} ({percentage:.0f}%)")
    
    # Рекомендации по улучшению
    print(f"\n{'='*80}")
    print("РЕКОМЕНДАЦИИ ПО УЛУЧШЕНИЮ")
    print(f"{'='*80}\n")
    
    weak_criteria = [k for k, (passed, total) in criteria_stats.items() if passed / total < 0.7]
    
    if not weak_criteria:
        print("✅ Все критерии на хорошем уровне!")
    else:
        print("⚠️ Нужно улучшить:")
        for criterion in weak_criteria:
            print(f"  - {QUALITY_CRITERIA[criterion]}")
        
        print("\nКОНКРЕТНЫЕ ШАГИ:")
        if 'specific_tools' in weak_criteria:
            print("  • Добавить в промпт требование называть конкретные инструменты/ресурсы")
            print("    Примеры: названия курсов, сервисов, Telegram-каналов, книг")
        if 'concrete' in weak_criteria:
            print("  • Требовать конкретные действия с глаголами: создай, напиши, позвони")
            print("  • Всегда включать цифры: сколько, когда, как часто")
        if 'time_bound' in weak_criteria:
            print("  • Обязательно указывать временные рамки для каждого совета")
        if 'measurable' in weak_criteria:
            print("  • Добавлять метрики успеха: как понять что сработало")
        if 'alternatives' in weak_criteria:
            print("  • Всегда предлагать минимум 2-3 варианта действий")
    
    session.close()
    
    return avg_percentage >= 70

if __name__ == "__main__":
    success = asyncio.run(run_advice_quality_test())
    sys.exit(0 if success else 1)
