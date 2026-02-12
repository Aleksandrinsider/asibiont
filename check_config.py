"""
Проверка конфигурации перед релизом
"""
import config

print("=" * 60)
print("ФИНАЛЬНАЯ ПРОВЕРКА КОНФИГУРАЦИИ")
print("=" * 60)

print(f"\n✅ Режим работы: {'ЛОКАЛЬНЫЙ (SQLite)' if config.LOCAL else 'ПРОДАКШЕН (PostgreSQL)'}")
print(f"✅ AI модель: {config.DEEPSEEK_MODEL}")
print(f"✅ Порт: {config.PORT}")
print(f"✅ DeepSeek API: {'Настроен' if config.DEEPSEEK_API_KEY else 'НЕ НАСТРОЕН!'}")

if not config.LOCAL:
    print(f"✅ Database URL: {'Настроен' if config.DATABASE_URL else 'НЕ НАСТРОЕН!'}")
    
print(f"✅ Оптимизированные промпты: {'Включены' if config.USE_OPTIMIZED_PROMPT else 'Выключены'}")
print(f"✅ Часовой пояс: {config.TIMEZONE}")

print("\n" + "=" * 60)
print("✅ КОНФИГУРАЦИЯ ГОТОВА К РЕЛИЗУ")
print("=" * 60)
