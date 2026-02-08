"""
Скрипт для полной очистки базы данных Railway PostgreSQL
ВНИМАНИЕ: Удаляет ВСЕ данные из всех таблиц, КРОМЕ промокодов!
"""

from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import Session
import sys

# Railway PostgreSQL credentials
DATABASE_URL = "postgresql://postgres:upZTbJrZvoxnoSPdUDaOwnLuOvnNSbML@nozomi.proxy.rlwy.net:52451/railway"

def get_all_tables(engine, exclude_tables=None):
    """Получить список всех таблиц в базе данных"""
    inspector = inspect(engine)
    all_tables = inspector.get_table_names()
    
    if exclude_tables:
        all_tables = [t for t in all_tables if t not in exclude_tables]
    
    return all_tables

def get_table_counts(session, tables):
    """Получить количество записей в каждой таблице"""
    counts = {}
    for table in tables:
        try:
            result = session.execute(text(f"SELECT COUNT(*) FROM {table}"))
            counts[table] = result.scalar()
        except Exception as e:
            counts[table] = f"Error: {e}"
    return counts

def clear_all_tables(session, tables):
    """Очистить все таблицы"""
    print("\n🗑️  Начинаю очистку таблиц...\n")
    
    # Отключаем временно проверку foreign keys для CASCADE удаления
    session.execute(text("SET session_replication_role = 'replica';"))
    
    cleared = []
    for table in tables:
        try:
            # Используем TRUNCATE для быстрого удаления
            session.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
            cleared.append(table)
            print(f"  ✅ Очищена: {table}")
        except Exception as e:
            print(f"  ❌ Ошибка при очистке {table}: {e}")
    
    # Включаем обратно проверку foreign keys
    session.execute(text("SET session_replication_role = 'origin';"))
    
    session.commit()
    return cleared

def main():
    print("=" * 70)
    print("🔥 КРИТИЧЕСКАЯ ОПЕРАЦИЯ: ПОЛНАЯ ОЧИСТКА БАЗЫ ДАННЫХ RAILWAY")
    print("💾 ПРОМОКОДЫ БУДУТ СОХРАНЕНЫ")
    print("=" * 70)
    
    try:
        # Подключение к БД
        print("\n🔗 Подключение к Railway PostgreSQL...")
        print(f"Host: nozomi.proxy.rlwy.net:52451")
        print(f"Database: railway\n")
        
        engine = create_engine(DATABASE_URL)
        
        with Session(engine) as session:
            # Получить список всех таблиц (кроме promo_codes)
            excluded_tables = ['promo_codes']
            tables = get_all_tables(engine, exclude_tables=excluded_tables)
            
            if not tables:
                print("❌ Таблицы не найдены в базе данных.")
                return
            
            print(f"📋 Найдено таблиц для очистки: {len(tables)}")
            print(f"💾 Таблицы, которые НЕ будут очищены: promo_codes\n")
            
            # Показать текущее состояние
            print("📊 Текущее состояние базы данных:")
            print("-" * 70)
            counts = get_table_counts(session, tables)
            
            total_records = 0
            for table, count in counts.items():
                if isinstance(count, int):
                    total_records += count
                    status = "📦" if count > 0 else "  "
                    print(f"{status} {table:30} {count:>10} записей")
                else:
                    print(f"⚠️  {table:30} {count}")
            
            print("-" * 70)
            print(f"Всего записей в БД: {total_records}")
            print()
            
            if total_records == 0:
                print("✅ База данных уже пуста. Нечего удалять.")
                return
            
            # ПЕРВОЕ ПОДТВЕРЖДЕНИЕ
            print("⚠️  ВЫ СОБИРАЕТЕСЬ УДАЛИТЬ ВСЕ ДАННЫЕ!")
            print("⚠️  Это действие НЕОБРАТИМО!")
            print("⚠️  Будут удалены:")
            print("   - Все пользователи")
            print("   - Все подписки")
            print("   - Все задачи")
            print("   - Все контакты")
            print("   - Все делегирования")
            print("   - Все события")
            print("   - Все алерты")
            print("\n✅ НЕ будут удалены:")
            print("   - Промокоды (promo_codes)")
            print()
            
            confirm1 = input("Введите 'DELETE ALL' для продолжения (или Enter для отмены): ").strip()
            
            if confirm1 != "DELETE ALL":
                print("\n❌ Операция отменена пользователем.")
                return
            
            # ВТОРОЕ ПОДТВЕРЖДЕНИЕ
            print()
            print(f"❗ ПОСЛЕДНЕЕ ПРЕДУПРЕЖДЕНИЕ!")
            print(f"❗ Будет удалено {total_records} записей из {len(tables)} таблиц")
            print()
            
            confirm2 = input("Введите 'YES I AM SURE' для окончательного подтверждения: ").strip()
            
            if confirm2 != "YES I AM SURE":
                print("\n❌ Операция отменена пользователем.")
                return
            
            # Выполнить очистку
            print()
            cleared_tables = clear_all_tables(session, tables)
            
            # Проверить результат
            print("\n📊 Проверка результата:")
            print("-" * 70)
            new_counts = get_table_counts(session, tables)
            
            all_clear = True
            for table, count in new_counts.items():
                if isinstance(count, int):
                    status = "✅" if count == 0 else "❌"
                    print(f"{status} {table:30} {count:>10} записей")
                    if count > 0:
                        all_clear = False
            
            print("-" * 70)
            
            if all_clear:
                print("\n✨ Успешно! Все таблицы очищены.")
                print(f"✨ Очищено таблиц: {len(cleared_tables)}")
                print("\n💾 База данных полностью пуста и готова к новым данным.")
            else:
                print("\n⚠️  Внимание! Некоторые таблицы не были полностью очищены.")
    
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
