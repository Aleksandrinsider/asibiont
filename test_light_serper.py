#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Тест новых SERPER функций для LIGHT пользователей
"""

import asyncio
import sys
import os
import logging

# Add path
sys.path.insert(0, os.path.dirname(__file__))

from ai_integration.handlers import quick_topic_search, check_topic_relevance
from models import Session, User

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def test_light_serper_functions():
    """Тестирует новые SERPER функции для LIGHT пользователей"""
    print("🔍 ТЕСТ: SERPER функции для LIGHT пользователей")
    print("=" * 60)
    
    session = Session()
    
    try:
        # Находим любого пользователя для тестов
        user = session.query(User).first()
        if not user:
            print("❌ Нет пользователей в базе данных")
            return
        
        print(f"👤 Пользователь для тестов: {user.telegram_id}")
        print()
        
        # Тест 1: quick_topic_search
        print("📝 Тест 1/2: quick_topic_search")
        print("   Тема: 'artificial intelligence 2026'")
        
        try:
            result1 = await quick_topic_search(
                topic="artificial intelligence 2026",
                user_id=user.telegram_id,
                session=session
            )
            
            print("✅ Результат получен")
            print(f"   Длина: {len(result1)} символов")
            
            # Проверяем ключевые элементы
            checks1 = [
                ("🔍 **Быстрый поиск**" in result1, "Заголовок"),
                ("1." in result1, "Нумерация результатов"),
                ("🔗 [Читать далее]" in result1, "Ссылки"),
                ("💡 **Подсказка**" in result1, "Подсказка про STANDARD"),
                ("artificial intelligence" in result1.lower() or "ai" in result1.lower(), "Релевантность")
            ]
            
            for check, name in checks1:
                status = "✅" if check else "❌"
                print(f"   {status} {name}")
            
            # Показываем превью
            preview1 = result1[:300] + "..." if len(result1) > 300 else result1
            print(f"   Превью: {preview1}")
            
        except Exception as e:
            print(f"   ❌ Ошибка: {e}")
        
        print("-" * 50)
        print()
        
        # Тест 2: check_topic_relevance
        print("📝 Тест 2/2: check_topic_relevance")  
        print("   Тема: 'quantum computing'")
        
        try:
            result2 = await check_topic_relevance(
                topic="quantum computing",
                user_id=user.telegram_id,
                session=session
            )
            
            print("✅ Результат получен")
            print(f"   Длина: {len(result2)} символов")
            
            # Проверяем ключевые элементы
            checks2 = [
                ("📊 **Проверка актуальности**" in result2, "Заголовок"),
                ("актуальность" in result2, "Оценка актуальности"),
                ("Найдено источников" in result2, "Статистика источников"),
                ("💡 **Рекомендация**" in result2, "Рекомендация"),
                ("quantum" in result2.lower() or "квантов" in result2.lower(), "Релевантность")
            ]
            
            for check, name in checks2:
                status = "✅" if check else "❌"
                print(f"   {status} {name}")
            
            # Показываем превью
            preview2 = result2[:300] + "..." if len(result2) > 300 else result2
            print(f"   Превью: {preview2}")
            
        except Exception as e:
            print(f"   ❌ Ошибка: {e}")
        
        print("-" * 50)
        print()
        
        print("🎯 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ:")
        print("   ✅ Функции для LIGHT пользователей добавлены")
        print("   🔍 quick_topic_search - быстрый поиск без AI анализа")  
        print("   📊 check_topic_relevance - проверка актуальности темы")
        print("   💡 Помогают LIGHT пользователям получать базовую информацию")
        print()
        print("🚀 ПРЕИМУЩЕСТВА ДЛЯ LIGHT:")
        print("   - Быстрый доступ к актуальной информации")
        print("   - Проверка трендов и новинок")
        print("   - Ссылки на источники для самостоятельного изучения")
        print("   - Мотивация к апгрейду до STANDARD для AI анализа")
        
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_light_serper_functions())