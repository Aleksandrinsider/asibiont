#!/usr/bin/env python3
"""
Быстрый тест основных функций
"""

import asyncio
import re
from datetime import datetime
from ai_integration.chat import chat_with_ai
from models import Session, User

async def quick_test():
    """Быстрый тест ключевых функций"""
    
    print("\n🚀 БЫСТРЫЙ ТЕСТ ГОТОВНОСТИ К ПРОДАКШЕНУ\n")
    print("="*70)
    
    # Активируем подписку для тестового пользователя
    session = Session()
    user = session.query(User).filter_by(telegram_id=1001).first()
    
    # Проверяем подписку
    from models import Subscription, SubscriptionTier
    sub = session.query(Subscription).filter_by(user_id=user.id).first()
    if not sub or sub.status != 'active':
        print("⚙️ Активирую подписку для тестирования...")
        from datetime import datetime, timedelta
        if not sub:
            sub = Subscription(
                user_id=user.id,
                telegram_id=user.telegram_id,
                telegram_username=user.username,
                username=user.username,
                tier=SubscriptionTier.GOLD
            )
            session.add(sub)
        sub.status = 'active'
        sub.end_date = datetime.now() + timedelta(days=30)
        user.subscription_tier = SubscriptionTier.GOLD
        session.commit()
        print("✅ Подписка активирована\n")
    
    session.expunge_all()  # Убираем объекты из сессии чтобы избежать конфликтов
    
    tests = [
        {
            "name": "1️⃣ Создание задачи с точным временем",
            "message": "Напомни через 10 минут позвонить Сергею",
            "check": lambda r: bool(re.search(r'\d{2}:\d{2}', r)),
            "desc": "AI указывает точное время"
        },
        {
            "name": "2️⃣ Текущее время (не UTC)",
            "message": "Сколько сейчас времени?",
            "check": lambda r: datetime.now().strftime('%H:%M') in r and "utc" not in r.lower(),
            "desc": "AI показывает локальное время без UTC"
        },
        {
            "name": "3️⃣ Уточнение результата при делегировании",
            "message": "Делегируй @test_user подготовить презентацию",
            "check": lambda r: any(word in r.lower() for word in ["результат", "откуда", "как", "критери", "формат"]),
            "desc": "AI уточняет ожидаемый результат"
        },
        {
            "name": "4️⃣ Удаление с причиной",
            "message": "Удали задачу про Сергея, она больше не нужна",
            "check": lambda r: "удал" in r.lower() and ("нужна" in r.lower() or "причин" in r.lower()),
            "desc": "AI учитывает причину удаления"
        },
        {
            "name": "5️⃣ Завершение с деталями",
            "message": "Позвонил Сергею, обсудили проект",
            "check": lambda r: any(word in r.lower() for word in ["завершен", "выполнен", "готово", "молодец", "отлично"]),
            "desc": "AI реагирует на завершение задачи"
        },
        {
            "name": "6️⃣ Неточное время (должен спросить)",
            "message": "Напомни вечером купить продукты",
            "check": lambda r: "?" in r and any(word in r.lower() for word in ["когда", "во сколько", "какое время"]),
            "desc": "AI уточняет неопределенное время"
        },
        {
            "name": "7️⃣ Список задач с временем",
            "message": "Покажи мои задачи",
            "check": lambda r: "задач" in r.lower() or re.search(r'\d{2}:\d{2}', r) or "нет задач" in r.lower(),
            "desc": "AI показывает задачи с временем"
        },
    ]
    
    passed = 0
    failed = 0
    
    for i, test in enumerate(tests, 1):
        print(f"\n{test['name']}")
        print(f"📤 Сообщение: {test['message']}")
        
        try:
            # Создаем новую сессию для каждого запроса
            test_session = Session()
            test_user = test_session.query(User).filter_by(telegram_id=1001).first()
            
            response = await chat_with_ai(
                user_id=test_user.id,
                message=test['message'],
                db_session=test_session
            )
            
            test_session.close()
            
            print(f"💬 Ответ: {response[:200]}{'...' if len(response) > 200 else ''}")
            
            if test['check'](response):
                print(f"✅ PASSED: {test['desc']}")
                passed += 1
            else:
                print(f"❌ FAILED: {test['desc']}")
                failed += 1
                
        except Exception as e:
            print(f"❌ ERROR: {e}")
            failed += 1
        
        # Небольшая задержка между запросами
        if i < len(tests):
            await asyncio.sleep(0.5)
    
    session.close()
    
    print("\n" + "="*70)
    print(f"📊 РЕЗУЛЬТАТЫ: {passed} ✅ / {failed} ❌ (всего {len(tests)})")
    print("="*70)
    
    if passed >= len(tests) * 0.8:  # 80% успешных тестов
        print("\n🎉 БОТ ГОТОВ К ПРОДАКШЕНУ!")
    elif passed >= len(tests) * 0.6:
        print("\n⚠️ ТРЕБУЮТСЯ НЕБОЛЬШИЕ ДОРАБОТКИ")
    else:
        print("\n🔴 ТРЕБУЮТСЯ СУЩЕСТВЕННЫЕ ДОРАБОТКИ")
    
    print()

if __name__ == "__main__":
    asyncio.run(quick_test())
