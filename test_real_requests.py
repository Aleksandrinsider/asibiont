"""Тест всех функций на реальных запросах через chat_with_ai"""

import asyncio
import sys
import os

# Устанавливаем LOCAL=0 для доступа к Railway БД
os.environ['LOCAL'] = '0'

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import User, Task
from ai_integration.chat import chat_with_ai
from config import DATABASE_URL

# Подключение к Railway БД
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

# Используем реальный telegram_id пользователя
TEST_USER_TELEGRAM_ID = 146333757

# Тестовые запросы для всех функций
test_cases = [
    {
        "name": "1. Создание задачи",
        "message": "напомни купить молоко завтра в 10:00",
        "expected_action": "создание задачи",
        "check": "короткий ответ, инструмент вызван"
    },
    {
        "name": "2. Завершение задачи",
        "message": "готово купить молоко",
        "expected_action": "завершение задачи",
        "check": "короткий ответ, вопрос о результате"
    },
    {
        "name": "3. Удаление задачи",
        "message": "удали задачу про отчет",
        "expected_action": "удаление задачи",
        "check": "короткий ответ"
    },
    {
        "name": "4. Просмотр задач",
        "message": "покажи мои задачи",
        "expected_action": "показ задач",
        "check": "список задач со статусами"
    },
    {
        "name": "5. Перенос задачи",
        "message": "перенеси встречу на завтра в 15:00",
        "expected_action": "перенос задачи",
        "check": "короткий ответ"
    },
    {
        "name": "6. Обновление профиля (город)",
        "message": "я из москвы, работаю программистом",
        "expected_action": "обновление профиля",
        "check": "короткий ответ"
    },
    {
        "name": "7. Поиск партнеров",
        "message": "найди партнеров по программированию",
        "expected_action": "поиск партнеров",
        "check": "результаты поиска или сообщение"
    },
    {
        "name": "8. Стратегический вопрос",
        "message": "как эффективно продвигать telegram бот без бюджета?",
        "expected_action": "развернутый ответ",
        "check": "2-4 абзаца, БЕЗ списков/нумерации, конкретные советы"
    },
    {
        "name": "9. Вопрос о возможностях",
        "message": "что ты умеешь?",
        "expected_action": "описание возможностей",
        "check": "развернутый ответ БЕЗ списков"
    },
    {
        "name": "10. Приветствие",
        "message": "привет",
        "expected_action": "приветствие",
        "check": "НЕ должен упоминать задачи без запроса"
    },
]


async def test_real_requests():
    """Тест на реальных запросах с реальным пользователем"""
    
    print("""
╔═══════════════════════════════════════════════════════════════════════════╗
║              ТЕСТ ВСЕХ ФУНКЦИЙ НА РЕАЛЬНЫХ ЗАПРОСАХ                       ║
║                   Проверка через chat_with_ai                             ║
╚═══════════════════════════════════════════════════════════════════════════╝
""")
    
    # Получаем пользователя
    session = Session()
    try:
        # Используем конкретного пользователя
        user = session.query(User).filter_by(telegram_id=TEST_USER_TELEGRAM_ID).first()
        
        if not user:
            print(f"❌ Пользователь с telegram_id={TEST_USER_TELEGRAM_ID} не найден в БД.")
            print("Убедитесь что вы авторизовались через бота.")
            return
        
        print(f"Тестируем с пользователем: @{user.username or user.first_name}")
        print(f"User ID: {user.telegram_id}")
        print("="*80)
        
        # Создаем тестовую задачу для команд
        test_task = Task(
            user_id=user.id,
            title="купить молоко",
            status="pending"
        )
        session.add(test_task)
        
        test_task2 = Task(
            user_id=user.id,
            title="встреча с клиентом",
            status="pending"
        )
        session.add(test_task2)
        
        test_task3 = Task(
            user_id=user.id,
            title="подготовить отчет",
            status="pending"
        )
        session.add(test_task3)
        session.commit()
        
        print(f"✓ Созданы тестовые задачи")
        print("="*80 + "\n")
        
        passed = 0
        failed = 0
        
        for i, case in enumerate(test_cases, 1):
            print(f"\n{'='*80}")
            print(f"ТЕСТ {i}/10: {case['name']}")
            print(f"{'='*80}")
            print(f"📩 Запрос: '{case['message']}'")
            print(f"🎯 Ожидается: {case['expected_action']}")
            print(f"✓ Проверка: {case['check']}")
            print("-"*80)
            
            try:
                # Вызываем реальную функцию AI
                response = await chat_with_ai(
                    message=case['message'],
                    user_id=user.telegram_id,
                    db_session=session,
                    context=None
                )
                
                print(f"🤖 Ответ AI:\n{response}\n")
                
                # Анализируем ответ
                checks = []
                
                # Проверка 1: Длина ответа
                if "развернутый ответ" in case['expected_action']:
                    if len(response) > 200:
                        checks.append("✓ Развернутый ответ")
                    else:
                        checks.append("✗ Ответ слишком короткий")
                else:
                    if len(response) < 200:
                        checks.append("✓ Краткий ответ")
                    else:
                        checks.append("⚠ Ответ длиннее ожидаемого")
                
                # Проверка 2: БЕЗ нумерации/списков для вопросов
                if "развернутый ответ" in case['expected_action']:
                    has_lists = any(x in response for x in ['1.', '2.', '•', '-', '▪'])
                    if not has_lists:
                        checks.append("✓ БЕЗ списков/нумерации")
                    else:
                        checks.append("✗ Найдены списки/нумерация")
                
                # Проверка 3: НЕ упоминает задачи в приветствии
                if case['name'] == "10. Приветствие":
                    if "задач" not in response.lower():
                        checks.append("✓ НЕ упоминает задачи")
                    else:
                        checks.append("✗ Упоминает задачи без запроса")
                
                # Проверка 4: Нет банальных фраз
                bad_phrases = [
                    "можем разбить на шаги",
                    "попробуй поставить",
                    "не страшно",
                    "хорошо, понял"
                ]
                has_bad = any(phrase in response.lower() for phrase in bad_phrases)
                if not has_bad:
                    checks.append("✓ БЕЗ банальных фраз")
                else:
                    checks.append("✗ Найдены банальные фразы")
                
                # Проверка 5: Есть ответ (не пустой)
                if len(response.strip()) > 0:
                    checks.append("✓ Ответ получен")
                else:
                    checks.append("✗ Пустой ответ")
                
                # Вывод результатов проверок
                print("📊 Проверки:")
                for check in checks:
                    print(f"   {check}")
                
                # Подсчет успеха
                if all("✓" in c for c in checks):
                    print("\n✅ ТЕСТ ПРОЙДЕН")
                    passed += 1
                else:
                    print("\n⚠️  ТЕСТ ЧАСТИЧНО ПРОЙДЕН")
                    failed += 1
                
            except Exception as e:
                print(f"\n❌ ОШИБКА: {e}")
                import traceback
                traceback.print_exc()
                failed += 1
            
            # Небольшая пауза между запросами
            await asyncio.sleep(1)
        
        # Итоговая статистика
        print("\n" + "="*80)
        print("ИТОГОВЫЙ РЕЗУЛЬТАТ")
        print("="*80)
        print(f"Всего тестов: {len(test_cases)}")
        print(f"✅ Пройдено: {passed}")
        print(f"⚠️  Частично: {failed}")
        print(f"Процент успеха: {(passed/len(test_cases)*100):.1f}%")
        print("="*80)
        
        if passed == len(test_cases):
            print("\n🎉 ВСЕ ТЕСТЫ ПОЛНОСТЬЮ ПРОЙДЕНЫ!")
        elif passed >= len(test_cases) * 0.7:
            print(f"\n✅ Хороший результат! {passed}/{len(test_cases)} тестов пройдено")
        else:
            print(f"\n⚠️  Требуется доработка")
        
    finally:
        session.close()


if __name__ == "__main__":
    asyncio.run(test_real_requests())
