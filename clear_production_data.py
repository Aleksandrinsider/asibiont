#!/usr/bin/env python3
"""
Скрипт для очистки производственной базы данных и Redis
"""
import asyncio
import sys
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
import redis.asyncio as aioredis
import os
from dotenv import load_dotenv

load_dotenv()


async def clear_database():
    """Очистка всех таблиц PostgreSQL"""
    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        print('❌ DATABASE_URL не найден в .env')
        return False
    
    # Конвертируем URL для asyncpg
    if db_url.startswith('postgresql://'):
        db_url = db_url.replace('postgresql://', 'postgresql+asyncpg://', 1)
    
    print(f'🔄 Подключаюсь к базе данных...')
    engine = create_async_engine(db_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        try:
            # Получаем все таблицы
            result = await session.execute(text("""
                SELECT tablename FROM pg_tables 
                WHERE schemaname = 'public'
            """))
            tables = [row[0] for row in result]
            
            if not tables:
                print('⚠️  Таблицы не найдены')
                return True
            
            print(f'📊 Найдено таблиц: {len(tables)}')
            
            # Удаляем данные из всех таблиц
            for table in tables:
                await session.execute(text(f'TRUNCATE TABLE "{table}" CASCADE'))
                print(f'  ✅ Очищена таблица: {table}')
            
            await session.commit()
            print(f'\n✨ База данных успешно очищена ({len(tables)} таблиц)')
            return True
            
        except Exception as e:
            print(f'❌ Ошибка при очистке БД: {e}')
            await session.rollback()
            return False
        finally:
            await engine.dispose()


async def clear_redis():
    """Очистка Redis кэша"""
    redis_url = os.getenv('REDIS_URL')
    if not redis_url:
        print('⚠️  REDIS_URL не найден в .env, пропускаю очистку Redis')
        return True
    
    try:
        print(f'🔄 Подключаюсь к Redis...')
        redis_client = aioredis.from_url(redis_url, decode_responses=True)
        
        # Получаем количество ключей
        keys_count = await redis_client.dbsize()
        print(f'📊 Найдено ключей в Redis: {keys_count}')
        
        if keys_count > 0:
            # Очищаем все данные
            await redis_client.flushdb()
            print(f'✨ Redis успешно очищен ({keys_count} ключей удалено)')
        else:
            print(f'✨ Redis уже пуст')
        
        await redis_client.close()
        return True
        
    except Exception as e:
        print(f'❌ Ошибка при очистке Redis: {e}')
        return False


async def main():
    print('=' * 60)
    print('🧹 ОЧИСТКА ПРОИЗВОДСТВЕННЫХ ДАННЫХ')
    print('=' * 60)
    
    # Проверяем режим
    local_mode = os.getenv("LOCAL", "False").lower() in ("true", "1", "yes")
    if local_mode:
        print('⚠️  ВНИМАНИЕ: Режим LOCAL=1 активен!')
        print('   Для production режима установите LOCAL=0 или удалите переменную\n')
    
    print('📋 Будут очищены:')
    print('   - Все таблицы PostgreSQL')
    print('   - Все данные в Redis')
    print('   - Все подписчики и пользователи')
    print('   - Вся история сообщений и задач\n')
    
    response = input('❓ Продолжить? (yes/no): ').strip().lower()
    if response not in ['yes', 'y']:
        print('❌ Операция отменена')
        return 1
    
    print('\n🚀 Начинаю очистку...\n')
    
    # Очищаем базу данных
    db_success = await clear_database()
    print()
    
    # Очищаем Redis
    redis_success = await clear_redis()
    print()
    
    # Итоговый результат
    print('=' * 60)
    if db_success and redis_success:
        print('✅ ОЧИСТКА ЗАВЕРШЕНА УСПЕШНО')
        print('=' * 60)
        print('\n💡 Следующие шаги:')
        print('   1. Убедитесь, что LOCAL=0 в .env (или удалите переменную)')
        print('   2. Бот готов к работе в production режиме')
        print('   3. Пользователи должны заново авторизоваться через /start')
        return 0
    else:
        print('⚠️  ОЧИСТКА ЗАВЕРШЕНА С ОШИБКАМИ')
        print('=' * 60)
        return 1


if __name__ == '__main__':
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print('\n❌ Операция прервана пользователем')
        sys.exit(1)
    except Exception as e:
        print(f'\n❌ Критическая ошибка: {e}')
        sys.exit(1)
