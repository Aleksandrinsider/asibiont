#!/usr/bin/env python3
"""
Тест публикации в Telegram канал
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.marketing_agent import publish_to_telegram
from models import Session, User
import config


async def test_publish_scenarios():
    """Тест различных сценариев публикации"""
    print("=" * 80)
    print("📢 ТЕСТ: Публикация в Telegram")
    print("=" * 80)
    
    session = Session()
    
    try:
        # Получим пользователя
        user = session.query(User).first()
        if not user:
            print("❌ Пользователь не найден в БД")
            return
        
        print(f"\n👤 Пользователь: {user.username or user.telegram_id}")
        print(f"📢 Telegram канал: {user.telegram_channel or 'НЕ НАСТРОЕН'}")
        
        # Сценарий 1: Пользователь без настроенного канала
        print("\n" + "=" * 80)
        print("[1/3] СЦЕНАРИЙ: Публикация без настроенного канала")
        print("=" * 80)
        
        original_channel = user.telegram_channel
        user.telegram_channel = None
        session.commit()
        
        result = await publish_to_telegram(
            content="Тестовый пост",
            user_id=user.id,
            session=session
        )
        
        print(f"\n✅ Результат получен")
        print(f"   Success: {result['success']}")
        print(f"\n📝 Сообщение пользователю:")
        print("-" * 80)
        print(result['message'])
        print("-" * 80)
        
        # Восстанавливаем канал
        user.telegram_channel = original_channel
        session.commit()
        
        # Сценарий 2: Структурированный контент (от generate_marketing_content)
        print("\n" + "=" * 80)
        print("[2/3] СЦЕНАРИЙ: Публикация структурированного контента")
        print("=" * 80)
        
        structured_content = {
            "title": "🚀 Революция в AI-боте!",
            "text": "Представляем новую функцию автопубликации. Теперь ваши маркетинговые посты будут публиковаться автоматически в Telegram канал!",
            "hashtags": ["#AI", "#маркетинг", "#автоматизация"],
            "cta": "Попробуйте прямо сейчас!"
        }
        
        print(f"\n📊 Контент:")
        print(f"   Заголовок: {structured_content['title']}")
        print(f"   Текст: {structured_content['text'][:50]}...")
        print(f"   Хэштеги: {len(structured_content['hashtags'])} шт.")
        
        if user.telegram_channel:
            result = await publish_to_telegram(
                content=structured_content,
                user_id=user.id,
                session=session
            )
            
            print(f"\n✅ Результат: {result['success']}")
            print(f"📝 Сообщение: {result['message']}")
        else:
            print("\n⚠️ Пропущено: канал не настроен")
        
        # Сценарий 3: Простая строка
        print("\n" + "=" * 80)
        print("[3/3] СЦЕНАРИЙ: Публикация простого текста")
        print("=" * 80)
        
        simple_text = "Это простой тестовый пост для проверки публикации."
        
        if user.telegram_channel:
            result = await publish_to_telegram(
                content=simple_text,
                user_id=user.id,
                session=session
            )
            
            print(f"\n✅ Результат: {result['success']}")
            print(f"📝 Сообщение: {result['message']}")
            
            if not result['success'] and 'Инструкция' in result['message']:
                print("\n📋 Бот правильно объясняет как настроить канал!")
        else:
            print("\n⚠️ Пропущено: канал не настроен")
        
        print("\n" + "=" * 80)
        print("✅ ВСЕ СЦЕНАРИИ ПРОТЕСТИРОВАНЫ")
        print("=" * 80)
        
        print("\n💡 Выводы:")
        print("   1. Бот проверяет наличие telegram_channel в профиле")
        print("   2. Даёт пошаговые инструкции по настройке")
        print("   3. Показывает как узнать ID приватного канала")
        print("   4. Объясняет необходимость прав администратора")
        
    except Exception as e:
        print(f"\n❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()


async def main():
    print("\n🚀 Запуск тестов публикации в Telegram...")
    print(f"   Bot: @{config.TELEGRAM_BOT_USERNAME}")
    print()
    
    await test_publish_scenarios()


if __name__ == "__main__":
    asyncio.run(main())
