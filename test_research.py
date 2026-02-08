#!/usr/bin/env python3
"""
Тест для research_topic - веб-поиск + AI-анализ через Serper API
"""
import asyncio
import sys
import os

# Добавим путь для импорта
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.marketing_agent import research_topic
from models import Session
import config


async def test_research():
    """Быстрый тест функции research_topic"""
    print("=" * 80)
    print("🔍 ТЕСТ: research_topic (Веб-поиск + AI-анализ)")
    print("=" * 80)
    
    # Проверим наличие API ключа
    if not config.SERPER_API_KEY:
        print("⚠️ SERPER_API_KEY не установлен!")
        print("   Функция будет работать в режиме AI-only (без веб-поиска)")
        print()
    
    session = Session()
    
    try:
        # Тест 1: Быстрое исследование (5 источников)
        print("\n[1/3] Тест: research_topic (quick) - 'AI-боты для бизнеса'")
        print("-" * 80)
        
        result = await research_topic(
            query="AI-боты для бизнеса в России 2026",
            depth="quick",
            user_id=1,
            session=session
        )
        
        print(f"✅ Результат получен")
        print(f"   Сообщение: {result.get('message', 'N/A')[:100]}...")
        
        if 'analysis' in result:
            analysis = result['analysis']
            print(f"\n   📊 Анализ:")
            print(f"      - Выводы: {len(analysis.get('insights', []))} шт.")
            print(f"      - Возможности: {len(analysis.get('opportunities', []))} шт.")
            print(f"      - Конкуренты: {len(analysis.get('competitors', []))} шт.")
            print(f"      - Тренды: {len(analysis.get('trends', []))} шт.")
            print(f"      - Действия: {len(analysis.get('actionable_steps', []))} шт.")
            
            if analysis.get('summary'):
                print(f"\n   📝 Краткая сводка:")
                print(f"      {analysis['summary'][:200]}...")
        
        print("\n" + "=" * 80)
        print("✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ")
        print("=" * 80)
        
        # Покажем первые 3 рекомендации
        if 'analysis' in result and 'actionable_steps' in result['analysis']:
            steps = result['analysis']['actionable_steps'][:3]
            print(f"\n🎯 Топ-3 рекомендации (должны были стать задачами):")
            for i, step in enumerate(steps, 1):
                print(f"   {i}. {step}")
        
    except Exception as e:
        print(f"\n❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()


async def main():
    """Точка входа"""
    print("\n🚀 Запуск теста research_topic...")
    print(f"   DeepSeek API: {'✅' if config.DEEPSEEK_API_KEY else '❌'}")
    print(f"   Serper API: {'✅' if config.SERPER_API_KEY else '❌ (будет работать без поиска)'}")
    print()
    
    await test_research()


if __name__ == "__main__":
    asyncio.run(main())
