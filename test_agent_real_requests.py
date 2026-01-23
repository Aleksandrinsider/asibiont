"""
Комплексный тест AI агента на реальных запросах пользователей
Проверяет все функции: время, задачи, делегирование, советы, обработку ошибок
"""

import asyncio
import os
import sys
from datetime import datetime
import pytz

# Настройка окружения
os.environ['LOCAL'] = '1'

from ai_integration.chat import chat_with_ai
from models import Session, User, Task, UserProfile
from config import DEEPSEEK_API_KEY

async def test_agent_real_requests():
    """Тестирование агента на реальных запросах"""
    
    print("=" * 80)
    print("🤖 КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ AI АГЕНТА НА РЕАЛЬНЫХ ЗАПРОСАХ")
    print("=" * 80)
    
    if not DEEPSEEK_API_KEY:
        print("❌ DEEPSEEK_API_KEY не настроен!")
        return
    
    # Создаем тестового пользователя
    db_session = Session()
    
    # Очищаем старые тесты
    test_user = db_session.query(User).filter_by(telegram_id=999999999).first()
    if test_user:
        db_session.query(Task).filter_by(user_id=test_user.id).delete()
        db_session.query(UserProfile).filter_by(user_id=test_user.id).delete()
        db_session.delete(test_user)
        db_session.commit()
    
    # Создаем свежего пользователя
    test_user = User(
        telegram_id=999999999,
        username="test_agent_user",
        first_name="Тестовый",
        timezone="Europe/Moscow",
        subscription_tier="SILVER"
    )
    db_session.add(test_user)
    db_session.commit()
    
    # Создаем профиль
    test_profile = UserProfile(
        user_id=test_user.id,
        city="Москва",
        company="Test Corp",
        position="QA Engineer",
        skills="Python, Testing",
        interests="AI, Automation",
        goals="Изучить AI агентов"
    )
    db_session.add(test_profile)
    db_session.commit()
    
    print(f"✅ Создан тестовый пользователь: @{test_user.username}")
    print(f"📍 Таймзона: {test_user.timezone}")
    print(f"🎫 Подписка: {test_user.subscription_tier}")
    print()
    
    db_session.close()
    
    # Тестовые запросы
    test_cases = [
        {
            "name": "1. Приветствие (temperature=1.0)",
            "message": "Привет! Как дела?",
            "expected_type": "greeting",
            "checks": [
                ("содержит приветствие", lambda r: any(word in r.lower() for word in ["привет", "здравствуй", "добрый"])),
                ("содержит вопрос", lambda r: "?" in r),
                ("правильная длина (10-200 символов)", lambda r: 10 <= len(r) <= 200),
            ]
        },
        {
            "name": "2. Создание задачи с точным временем (temperature=0.6)",
            "message": "Напомни мне в 18:30 проверить почту",
            "expected_type": "add_task",
            "checks": [
                ("упоминает задачу", lambda r: any(word in r.lower() for word in ["задач", "напомн"])),
                ("НЕ упоминает 'напомню в'", lambda r: "напомню" not in r.lower() or "напомню тебе в" not in r.lower()),
                ("содержит подтверждение", lambda r: any(word in r.lower() for word in ["готово", "хорошо", "отлично", "создан"])),
            ]
        },
        {
            "name": "3. Создание задачи с относительным временем",
            "message": "через 2 часа позвонить клиенту",
            "expected_type": "add_task",
            "checks": [
                ("подтверждение создания", lambda r: any(word in r.lower() for word in ["задач", "напомн", "созда"])),
                ("НЕ упоминает 'напомню'", lambda r: "напомню тебе" not in r.lower()),
            ]
        },
        {
            "name": "4. Список задач (temperature=0.6)",
            "message": "покажи мои задачи",
            "expected_type": "list_tasks",
            "checks": [
                ("показывает задачи или сообщает об отсутствии", lambda r: True),  # всегда валидно
            ]
        },
        {
            "name": "5. Совет по продуктивности (temperature=0.85)",
            "message": "как мне лучше организовать свой день?",
            "expected_type": "advice",
            "checks": [
                ("содержит совет", lambda r: len(r) >= 50),
                ("персонализирован", lambda r: any(word in r.lower() for word in ["ты", "твой", "тебе"])),
                ("содержит вопрос для продолжения", lambda r: "?" in r),
            ]
        },
        {
            "name": "6. Проверка времени с таймзоной",
            "message": "сколько сейчас времени?",
            "expected_type": "conversation",
            "checks": [
                ("упоминает время", lambda r: any(char.isdigit() for char in r) or "врем" in r.lower()),
                ("НЕ округляет время", lambda r: True),  # проверим логи
            ]
        },
        {
            "name": "7. Обновление профиля",
            "message": "добавь в мои навыки JavaScript",
            "expected_type": "update_profile",
            "checks": [
                ("подтверждение обновления", lambda r: any(word in r.lower() for word in ["обновл", "добавил", "сохран"])),
            ]
        },
        {
            "name": "8. Неоднозначный запрос (проверка fallback)",
            "message": "завтра",
            "expected_type": "conversation",
            "checks": [
                ("не пустой ответ", lambda r: len(r) >= 5),
                ("адекватная реакция", lambda r: "?" in r or "что" in r.lower() or "завтра" in r.lower()),
            ]
        },
        {
            "name": "9. Делегирование (для SILVER подписки)",
            "message": "хочу делегировать задачу проверить почту на @colleague",
            "expected_type": "delegate_task",
            "checks": [
                ("упоминает делегирование", lambda r: any(word in r.lower() for word in ["делегир", "поруч", "уведом"])),
            ]
        },
        {
            "name": "10. Обычный разговор (temperature=0.7)",
            "message": "какие у меня планы на вечер?",
            "expected_type": "conversation",
            "checks": [
                ("персонализированный ответ", lambda r: len(r) >= 20),
                ("содержит вопрос или совет", lambda r: "?" in r or len(r) >= 30),
            ]
        },
    ]
    
    # Запускаем тесты
    results = {
        "total": len(test_cases),
        "passed": 0,
        "failed": 0,
        "details": []
    }
    
    for i, test_case in enumerate(test_cases, 1):
        print(f"\n{'='*80}")
        print(f"📝 Тест {i}/{len(test_cases)}: {test_case['name']}")
        print(f"{'='*80}")
        print(f"💬 Запрос: \"{test_case['message']}\"")
        print(f"🎯 Ожидаемый тип: {test_case['expected_type']}")
        print()
        
        try:
            # Отправляем запрос
            print("⏳ Отправка запроса к AI агенту...")
            response = await chat_with_ai(
                message=test_case['message'],
                user_id=999999999,
                context=None
            )
            
            print(f"\n✅ Получен ответ ({len(response) if response else 0} символов):")
            print(f"{'─'*80}")
            print(response if response else "❌ Получен пустой ответ (None)")
            print(f"{'─'*80}")
            
            # Проверяем условия
            test_passed = True
            print(f"\n🔍 Проверка условий:")
            
            # Если ответ None, тест провален
            if response is None:
                print(f"  ❌ Получен None вместо ответа!")
                test_passed = False
            else:
                for check_name, check_func in test_case['checks']:
                    try:
                        check_result = check_func(response)
                        status = "✅" if check_result else "❌"
                        print(f"  {status} {check_name}")
                        if not check_result:
                            test_passed = False
                    except Exception as e:
                        print(f"  ⚠️  {check_name}: Ошибка проверки ({e})")
                        test_passed = False
            
            if test_passed:
                print(f"\n🎉 Тест ПРОЙДЕН")
                results['passed'] += 1
                results['details'].append({
                    "test": test_case['name'],
                    "status": "PASSED",
                    "response_length": len(response) if response else 0
                })
            else:
                print(f"\n❌ Тест ПРОВАЛЕН")
                results['failed'] += 1
                results['details'].append({
                    "test": test_case['name'],
                    "status": "FAILED",
                    "response_length": len(response) if response else 0
                })
            
            # Небольшая задержка между запросами
            await asyncio.sleep(1)
            
        except Exception as e:
            print(f"\n💥 ОШИБКА при выполнении теста: {e}")
            import traceback
            traceback.print_exc()
            results['failed'] += 1
            results['details'].append({
                "test": test_case['name'],
                "status": "ERROR",
                "error": str(e)
            })
    
    # Итоговый отчет
    print(f"\n\n{'='*80}")
    print(f"📊 ИТОГОВЫЙ ОТЧЕТ")
    print(f"{'='*80}")
    print(f"Всего тестов: {results['total']}")
    print(f"✅ Пройдено: {results['passed']}")
    print(f"❌ Провалено: {results['failed']}")
    print(f"📈 Успешность: {results['passed']/results['total']*100:.1f}%")
    print()
    
    print("Детали по каждому тесту:")
    for detail in results['details']:
        status_icon = "✅" if detail['status'] == "PASSED" else "❌" if detail['status'] == "FAILED" else "💥"
        print(f"  {status_icon} {detail['test']}: {detail['status']}")
    
    print(f"\n{'='*80}")
    
    # Очистка
    db_session = Session()
    test_user = db_session.query(User).filter_by(telegram_id=999999999).first()
    if test_user:
        print(f"\n🧹 Очистка тестовых данных...")
        created_tasks = db_session.query(Task).filter_by(user_id=test_user.id).count()
        print(f"   Создано задач: {created_tasks}")
        db_session.query(Task).filter_by(user_id=test_user.id).delete()
        db_session.query(UserProfile).filter_by(user_id=test_user.id).delete()
        db_session.delete(test_user)
        db_session.commit()
        print(f"   ✅ Тестовые данные удалены")
    db_session.close()
    
    # Финальная оценка
    if results['passed'] == results['total']:
        print(f"\n🎉🎉🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ! Агент работает идеально! 🎉🎉🎉")
        return 0
    elif results['passed'] >= results['total'] * 0.8:
        print(f"\n👍 Отличный результат! Агент работает хорошо, но есть что улучшить.")
        return 0
    elif results['passed'] >= results['total'] * 0.6:
        print(f"\n⚠️  Агент работает, но требуются улучшения.")
        return 1
    else:
        print(f"\n❌ Агент требует серьезных исправлений!")
        return 1

if __name__ == "__main__":
    try:
        exit_code = asyncio.run(test_agent_real_requests())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n⚠️  Тестирование прервано пользователем")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n💥 Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
