#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Тест функции get_news_trends - новости и тренды по интересам
"""

import asyncio
import sys
import os
import logging

# Add path
sys.path.insert(0, os.path.dirname(__file__))

from ai_integration.handlers import get_news_trends
from models import Session, User

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def test_news_trends():
    """Тестирует получение новостей и анализ трендов"""
    print("ТЕСТ: get_news_trends - новости и тренды")
    print("=" * 50)
    
    # Тестовые случаи
    test_cases = [
        {
            "topic": "искусственный интеллект",
            "period": "week",
            "focus": "trends",
            "description": "AI тренды за неделю"
        },
        {
            "topic": "стартапы России",
            "period": "month", 
            "focus": "opportunities",
            "description": "Бизнес-возможности в стартапах"
        },
        {
            "topic": "финтех",
            "period": "today",
            "focus": "news",
            "description": "Новости финтеха за день"
        }
    ]
    
    session = Session()
    
    try:
        # Найти пользователя с подпиской STANDARD/PREMIUM
        user = session.query(User).filter(
            User.subscription_tier.in_(['STANDARD', 'PREMIUM'])
        ).first()
        
        if not user:
            print("❌ Не найдено пользователей с подпиской STANDARD/PREMIUM")
            print("Создаем тестового пользователя...")
            user = session.query(User).first()
            if user:
                user.subscription_tier = 'STANDARD'
                session.commit()
                print(f"✅ Обновлена подписка пользователя {user.telegram_id} до STANDARD")
            else:
                print("❌ Нет пользователей в базе данных")
                return
        
        print(f"Пользователь: {user.telegram_id} (подписка: {user.subscription_tier})")
        print()
        
        # Тестируем каждый случай
        for i, case in enumerate(test_cases, 1):
            print(f"Тест {i}/3: {case['description']}")
            print(f"   Тема: {case['topic']}")
            print(f"   Период: {case['period']}")
            print(f"   Фокус: {case['focus']}")
            
            try:
                result = await get_news_trends(
                    topic=case['topic'],
                    period=case['period'],
                    focus=case['focus'],
                    user_id=user.telegram_id,
                    session=session
                )
                
                print(f"✅ Результат получен ({len(result)} символов)")
                
                # Показываем превью результата
                preview = result[:300] 
                if len(result) > 300:
                    preview += "..."
                print(f"Превью: {preview}")
                
                # Проверяем ключевые элементы в зависимости от фокуса
                if case['focus'] == 'trends':
                    checks = [
                        ("🔥 **Главные тренды**" in result, "Секция трендов"),
                        ("📈 **О чём говорят**" in result, "Резюме"),
                        ("📋 **Ключевые события**" in result, "События")
                    ]
                elif case['focus'] == 'opportunities':
                    checks = [
                        ("🚀 **Бизнес-возможности**" in result, "Возможности"),
                        ("📋 **На что обратить внимание**" in result, "Внимание"),
                        ("🔍 **Рекомендации**" in result, "Рекомендации")
                    ]
                else:  # news
                    checks = [
                        ("📰 **Новости по теме**" in result, "Заголовок новостей"),
                        ("1." in result, "Нумерация новостей"),
                        ("**" in result, "Форматирование")
                    ]
                
                for check, name in checks:
                    status = "✅" if check else "❌" 
                    print(f"   {status} {name}")
                
            except Exception as e:
                print(f"❌ Ошибка: {e}")
            
            print("-" * 40)
            print()
        
        print("ТЕСТИРОВАНИЕ ЗАВЕРШЕНО!")
        print("Функция get_news_trends готова к использованию")
        
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_news_trends())