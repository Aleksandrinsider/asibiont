"""
Скрипт для очистки дубликатов в интересах пользователей
Запуск: python cleanup_duplicate_interests.py
"""

import asyncio
import asyncpg
import os
from config import DATABASE_URL

async def cleanup_duplicate_interests():
    """Удаляет дубликаты из поля interests для всех профилей"""
    
    # Парсим DATABASE_URL
    # Формат: postgresql://user:pass@host:port/database
    if not DATABASE_URL or not DATABASE_URL.startswith('postgresql'):
        print("❌ Скрипт работает только с PostgreSQL")
        return
    
    try:
        # Подключаемся к БД
        conn = await asyncpg.connect(DATABASE_URL)
        
        # Получаем все профили с интересами
        profiles = await conn.fetch("SELECT user_id, interests FROM user_profiles WHERE interests IS NOT NULL AND interests != ''")
        
        updated_count = 0
        
        for row in profiles:
            user_id = row['user_id']
            interests = row['interests']
            
            if not interests:
                continue
                
            # Разбиваем на список
            interests_list = [i.strip() for i in interests.split(',') if i.strip()]
            
            # Удаляем дубликаты (case-insensitive)
            seen = set()
            unique_interests = []
            for interest in interests_list:
                if interest.lower() not in seen:
                    unique_interests.append(interest)
                    seen.add(interest.lower())
            
            # Обновляем только если были изменения
            new_interests = ', '.join(unique_interests)
            if new_interests != interests:
                await conn.execute(
                    "UPDATE user_profiles SET interests = $1 WHERE user_id = $2",
                    new_interests, user_id
                )
                updated_count += 1
                print(f"User ID {user_id}:")
                print(f"  До:    {interests}")
                print(f"  После: {new_interests}")
                print()
        
        if updated_count > 0:
            print(f"\n✅ Обновлено профилей: {updated_count}")
        else:
            print("\n✅ Дубликатов не найдено")
            
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if 'conn' in locals():
            await conn.close()

if __name__ == '__main__':
    print("Начинаем очистку дубликатов в интересах...\n")
    asyncio.run(cleanup_duplicate_interests())
