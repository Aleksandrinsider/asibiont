#!/usr/bin/env python3
"""
Упрощенный тест делегирования с полными деталями сразу
"""

import asyncio
import os
from datetime import datetime
from ai_integration.chat import chat_with_ai
from models import Session, User, Task

os.environ['FREE_ACCESS_MODE'] = '1'

async def simple_delegation_test():
    print("\n🎯 УПРОЩЕННЫЙ ТЕСТ ДЕЛЕГИРОВАНИЯ\n")
    print("="*70)
    
    session = Session()
    user = session.query(User).filter_by(telegram_id=1002).first()
    user_id = user.id
    session.close()
    
    # ТЕСТ 1: Делегирование с ПОЛНЫМИ деталями сразу
    print("\n1️⃣ Делегирование с полными деталями сразу")
    print("   Сообщение: Делегируй @test_user3 подготовить отчет по продажам до завтра 15:00. Отчет нужен в Excel, отправить в Telegram, должен содержать цифры за январь с графиками")
    
    s1 = Session()
    u1 = s1.query(User).filter_by(id=user_id).first()
    response1 = await chat_with_ai(
        user_id=u1.id,
        message="Делегируй @test_user3 подготовить отчет по продажам до завтра 15:00. Отчет нужен в Excel, отправить в Telegram, должен содержать цифры за январь с графиками",
        db_session=s1
    )
    
    task = s1.query(Task).filter(
        Task.user_id == u1.id,
        Task.delegated_to_username.isnot(None)
    ).order_by(Task.created_at.desc()).first()
    
    print(f"\n   Ответ AI: {response1[:200]}...")
    
    if task:
        print(f"\n   ✅ Задача делегирована!")
        print(f"      • ID: {task.id}")
        print(f"      • Название: {task.title}")
        print(f"      • Кому: {task.delegated_to_username}")
        print(f"      • Детали: {task.delegation_details[:100] if task.delegation_details else 'нет'}...")
        print(f"      • Статус: {task.delegation_status}")
    else:
        print(f"\n   ❌ Задача НЕ делегирована")
        if "результат" in response1.lower() or "критери" in response1.lower():
            print(f"      Причина: AI все еще уточняет детали (НЕ ДОЛЖЕН при таком запросе)")
        elif "bronze" in response1.lower() or "silver" in response1.lower():
            print(f"      Причина: Проблема с подпиской")
    
    s1.close()
    
    # ТЕСТ 2: Исполнитель видит задачу
    if task:
        print(f"\n2️⃣ Исполнитель просматривает делегированные задачи")
        print("   Сообщение: Покажи мои делегированные задачи")
        
        s2 = Session()
        executor = s2.query(User).filter_by(telegram_id=1003).first()
        response2 = await chat_with_ai(
            user_id=executor.id,
            message="Покажи мои делегированные задачи",
            db_session=s2
        )
        
        print(f"\n   Ответ AI: {response2[:300]}...")
        
        if "отчет" in response2.lower() or "продаж" in response2.lower():
            print(f"\n   ✅ Исполнитель видит задачу")
        else:
            print(f"\n   ❌ Задача не отображается")
        
        s2.close()
    
    # ТЕСТ 3: Принятие задачи
    if task:
        print(f"\n3️⃣ Исполнитель принимает задачу")
        print("   Сообщение: Принять задачу про отчет")
        
        s3 = Session()
        executor = s3.query(User).filter_by(telegram_id=1003).first()
        response3 = await chat_with_ai(
            user_id=executor.id,
            message="Принять задачу про отчет",
            db_session=s3
        )
        
        task_check = s3.query(Task).filter_by(id=task.id).first()
        
        print(f"\n   Ответ AI: {response3[:200]}...")
        print(f"   Статус в БД: {task_check.delegation_status if task_check else 'не найдена'}")
        
        if task_check and task_check.delegation_status == 'accepted':
            print(f"\n   ✅ Задача принята")
        else:
            print(f"\n   ❌ Задача не принята или статус не обновлен")
        
        s3.close()
    
    print("\n" + "="*70)
    print("\n📊 ВЫВОДЫ:")
    print("   • AI должен делегировать сразу если все детали предоставлены")
    print("   • AI должен уточнять только если деталей недостаточно")
    print("   • Контекст между сообщениями в реальном боте сохраняется через историю\n")

if __name__ == "__main__":
    asyncio.run(simple_delegation_test())
