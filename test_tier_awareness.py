"""
Тест: понимает ли агент тарифы и предлагает функции правильно

Проверяем:
1. Агент знает какие функции доступны на каждом тарифе
2. Предлагает функции только если они доступны пользователю
3. Мягко предлагает апгрейд если функция недоступна
"""

import asyncio
import os
from datetime import datetime

os.environ['LOCAL'] = '1'
os.environ['DATABASE_URL'] = 'sqlite:///task_bot.db'

from models import Session, User, UserProfile, SubscriptionTier
from ai_integration.autonomous_agent import chat_with_ai
from ai_integration.prompts import get_extended_system_prompt
import pytz


def create_test_user(telegram_id, tier, username):
    """Создаёт тестового пользователя с определённым тарифом"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=telegram_id).first()
        if not user:
            user = User(
                telegram_id=telegram_id,
                username=username,
                subscription_tier=getattr(SubscriptionTier, tier),
                timezone='Europe/Moscow'
            )
            session.add(user)
        else:
            user.subscription_tier = getattr(SubscriptionTier, tier)
        session.commit()
        
        # Профиль
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            profile = UserProfile(
                user_id=user.id,
                bio="Тестовый пользователь",
                goals="Развитие бизнеса"
            )
            session.add(profile)
            session.commit()
        
        return user.telegram_id
    finally:
        session.close()


async def test_tier_awareness():
    """Основной тест проверки понимания тарифов"""
    
    print("="*70)
    print("ТЕСТ: ПОНИМАНИЕ ТАРИФОВ И ФУНКЦИЙ")
    print("="*70)
    
    # Создаем тестовых пользователей
    light_user_id = create_test_user(111111111, 'LIGHT', 'test_light')
    standard_user_id = create_test_user(222222222, 'STANDARD', 'test_standard')
    premium_user_id = create_test_user(333333333, 'PREMIUM', 'test_premium')
    
    # Тестовые запросы для каждого тарифа
    test_scenarios = [
        {
            "user_id": light_user_id,
            "tier": "LIGHT",
            "message": "не знаю как продвигать мой стартап",
            "expected": "должен упомянуть что маркетинг на STANDARD/PREMIUM"
        },
        {
            "user_id": light_user_id,
            "tier": "LIGHT",
            "message": "нужно найти дизайнера",
            "expected": "должен предложить find_partners (доступно на LIGHT)"
        },
        {
            "user_id": standard_user_id,
            "tier": "STANDARD",
            "message": "не знаю как продвигать мой стартап",
            "expected": "должен предложить research_topic (доступно на STANDARD)"
        },
        {
            "user_id": standard_user_id,
            "tier": "STANDARD",
            "message": "много задач, хочу делегировать",
            "expected": "должен упомянуть что делегирование на PREMIUM"
        },
        {
            "user_id": premium_user_id,
            "tier": "PREMIUM",
            "message": "хочу автоматизировать публикацию постов",
            "expected": "должен предложить автономный маркетинг (PREMIUM)"
        },
        {
            "user_id": premium_user_id,
            "tier": "PREMIUM",
            "message": "много задач, нужна помощь",
            "expected": "должен предложить делегирование (доступно на PREMIUM)"
        }
    ]
    
    results = {"passed": 0, "failed": 0, "total": len(test_scenarios)}
    
    for i, scenario in enumerate(test_scenarios, 1):
        print(f"\n{'='*70}")
        print(f"SCENARIO {i}/{len(test_scenarios)}: {scenario['tier']}")
        print(f"{'='*70}")
        print(f"Пользователь: {scenario['message']}")
        print(f"Ожидается: {scenario['expected']}")
        print(f"\nОтвет агента:")
        print("-"*70)
        
        try:
            # Генерируем ответ через AI
            session = Session()
            user = session.query(User).filter_by(telegram_id=scenario['user_id']).first()
            
            response = await chat_with_ai(
                message=scenario['message'],
                user_id=scenario['user_id'],
                db_session=session
            )
            
            # Не выводим полный ответ из-за проблем с кодировкой Windows
            response_text = response['response'].lower()
            print(f"Response length: {len(response['response'])} chars")
            
            # Простая проверка: есть ли упоминания нужных терминов
            checks = []
            
            if scenario['tier'] == 'LIGHT' and 'продвигать' in scenario['message']:
                # LIGHT не должен получить прямое предложение маркетинга
                has_tier_mention = 'standard' in response_text or 'тариф' in response_text
                checks.append(('Упоминание тарифа для недоступных функций', has_tier_mention))
            
            if scenario['tier'] == 'LIGHT' and 'дизайнера' in scenario['message']:
                # LIGHT должен получить предложение поиска партнеров
                has_partners = 'партнёр' in response_text or 'найти' in response_text or 'поиск' in response_text
                checks.append(('Предложение find_partners', has_partners))
            
            if scenario['tier'] == 'STANDARD' and 'продвигать' in scenario['message']:
                # STANDARD должен получить предложение маркетинга
                has_marketing = any(word in response_text for word in ['исследов', 'рынок', 'конкурент', 'пост', 'маркетинг'])
                checks.append(('Предложение маркетинга', has_marketing))
            
            if scenario['tier'] == 'STANDARD' and 'делегировать' in scenario['message']:
                # STANDARD не должен получить делегирование, но упоминание Premium
                has_premium_mention = 'premium' in response_text or 'тариф' in response_text
                checks.append(('Упоминание Premium для делегирования', has_premium_mention))
            
            if scenario['tier'] == 'PREMIUM' and 'автоматизировать' in scenario['message']:
                # PREMIUM должен получить предложение автономного маркетинга
                has_auto = any(word in response_text for word in ['автомат', 'автопилот', 'сам', 'расписан'])
                checks.append(('Предложение автономного маркетинга', has_auto))
            
            if scenario['tier'] == 'PREMIUM' and ('делегировать' in scenario['message'] or 'много задач' in scenario['message']):
                # PREMIUM должен получить предложение делегирования
                has_delegate = 'делегир' in response_text or 'передать' in response_text or 'поручить' in response_text
                checks.append(('Предложение делегирования', has_delegate))
            
            # Выводим результаты проверок
            print("\n" + "-"*70)
            print("Проверки:")
            all_passed = True
            for check_name, check_result in checks:
                status = "[OK]" if check_result else "[FAIL]"
                print(f"{status} {check_name}: {check_result}")
                if not check_result:
                    all_passed = False
            
            if all_passed and checks:
                print("\n[OK] Scenario PASSED")
                results['passed'] += 1
            else:
                print("\n[WARNING] Scenario NOT PASSED or needs manual check")
                results['failed'] += 1
            
            session.close()
            
        except Exception as e:
            print(f"\n[ERROR]: {str(e)[:100]}")
            results['failed'] += 1
    
    # Итоги
    print("\n" + "="*70)
    print("ИТОГОВЫЕ РЕЗУЛЬТАТЫ")
    print("="*70)
    print(f"OK: {results['passed']}/{results['total']}")
    print(f"FAIL: {results['failed']}/{results['total']}")
    print(f"SUCCESS: {(results['passed']/results['total']*100):.1f}%")
    
    if results['passed'] == results['total']:
        print("\nVSE TESTY PASSED! Agent understands tiers.")
    else:
        print("\nSome tests failed. Check prompts.")


async def test_prompt_structure():
    """Тест структуры промпта: есть ли карта тарифов"""
    
    print("\n" + "="*70)
    print("CHECK: PROMPT STRUCTURE")
    print("="*70)
    
    now = datetime.now(pytz.timezone('Europe/Moscow'))
    
    # Генерируем промпт для каждого тарифа
    for tier in ['LIGHT', 'STANDARD', 'PREMIUM']:
        prompt = get_extended_system_prompt(
            user_now=now,
            current_time_str=now.strftime('%H:%M'),
            current_date_str=now.strftime('%d.%m.%Y'),
            user_username=f"test_{tier.lower()}",
            mentions_str="",
            user_memory="",
            subscription_tier=tier
        )
        
        print(f"\nPROMPT for {tier}:")
        
        # Проверяем наличие ключевых элементов
        checks = {
            "Есть карта тарифов": "ТАРИФЫ И ВОЗМОЖНОСТИ" in prompt,
            f"Упоминается LIGHT": "LIGHT" in prompt and "3000₽" in prompt,
            f"Упоминается STANDARD": "STANDARD" in prompt and "9000₽" in prompt,
            f"Упоминается PREMIUM": "PREMIUM" in prompt and "27000₽" in prompt,
            "Есть проактивные предложения": "ПРОАКТИВНЫЕ ПРЕДЛОЖЕНИЯ" in prompt,
            "Есть примеры с тарифами": "ЕСЛИ LIGHT" in prompt or "ЕСЛИ STANDARD" in prompt or "ЕСЛИ PREMIUM" in prompt,
            f"Subscription tier передан": f"Подписка: {tier}" in prompt
        }
        
        for check_name, result in checks.items():
            status = "[OK]" if result else "[FAIL]"
            print(f"  {status} {check_name}")
        
        all_passed = all(checks.values())
        if all_passed:
            print(f"  OK: Prompt for {tier} correct")
        else:
            print(f"  WARNING: Issues in prompt for {tier}")


if __name__ == "__main__":
    print("\nЗАПУСК ТЕСТОВ ПОНИМАНИЯ ТАРИФОВ\n")
    asyncio.run(test_prompt_structure())
    print("\n" + "="*70 + "\n")
    asyncio.run(test_tier_awareness())
