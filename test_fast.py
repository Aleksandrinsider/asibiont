import sys
import os
import subprocess
import time
from pathlib import Path

def run_command(cmd, description):
    """Запускает команду и возвращает результат"""
    print(f"\n🔍 {description}...")
    start_time = time.time()
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        elapsed = time.time() - start_time
        if result.returncode == 0:
            print(f"✅ {description} - УСПЕШНО ({elapsed:.2f} сек)")
            return True
        else:
            print(f"❌ {description} - ОШИБКА ({elapsed:.2f} сек)")
            print(f"   Вывод: {result.stderr[:200]}...")
            return False
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"❌ {description} - ИСКЛЮЧЕНИЕ ({elapsed:.2f} сек): {str(e)}")
        return False

def test_syntax():
    """Проверяет синтаксис всех Python файлов"""
    success_count = 0
    total_count = 0

    # Основные файлы
    main_files = [
        'main.py', 'config.py', 'models.py', 'handlers.py', 'payments.py',
        'reminder_service.py', 'subscription_service.py', 'auto_post_service.py'
    ]

    for file in main_files:
        if os.path.exists(file):
            total_count += 1
            if run_command(f'python -m py_compile {file}', f'Синтаксис {file}'):
                success_count += 1

    # Файлы в ai_integration
    ai_dir = Path('ai_integration')
    if ai_dir.exists():
        for py_file in ai_dir.rglob('*.py'):
            total_count += 1
            if run_command(f'python -m py_compile {py_file}', f'Синтаксис {py_file.name}'):
                success_count += 1

    print(f"\n📊 Синтаксис: {success_count}/{total_count} файлов ОК")
    return success_count == total_count

def test_imports():
    """Проверяет основные импорты"""
    tests = [
        ('import config', 'Импорт config'),
        ('import models', 'Импорт models'),
        ('import ai_integration.chat', 'Импорт chat'),
        ('import ai_integration.router', 'Импорт router'),
        ('from ai_integration.commands.conversation import ConversationCommand', 'Импорт ConversationCommand'),
    ]

    success_count = 0
    for import_stmt, description in tests:
        try:
            exec(import_stmt)
            print(f"✅ {description} - УСПЕШНО")
            success_count += 1
        except Exception as e:
            print(f"❌ {description} - ОШИБКА: {str(e)}")

    print(f"\n📊 Импорты: {success_count}/{len(tests)} ОК")
    return success_count == len(tests)

def test_basic_functionality():
    """Проверяет базовую функциональность без AI"""
    success_count = 0
    total_count = 0

    # Тест создания пользователя
    total_count += 1
    try:
        from models import User
        import pytz
        from datetime import datetime

        user = User(
            telegram_id=999999,
            username='fast_test_user',
            timezone='Europe/Moscow',
            created_at=datetime.now(pytz.UTC)
        )
        print("✅ Создание User - УСПЕШНО")
        success_count += 1
    except Exception as e:
        print(f"❌ Создание User - ОШИБКА: {str(e)}")

    # Тест роутера
    total_count += 1
    try:
        from ai_integration.router import CommandRouter
        router = CommandRouter()
        print("✅ Создание CommandRouter - УСПЕШНО")
        success_count += 1
    except Exception as e:
        print(f"❌ Создание CommandRouter - ОШИБКА: {str(e)}")

    # Тест conversation команды
    total_count += 1
    try:
        from ai_integration.commands.conversation import ConversationCommand
        from datetime import datetime
        cmd = ConversationCommand("test message", message_time=datetime.now())
        print("✅ Создание ConversationCommand - УСПЕШНО")
        success_count += 1
    except Exception as e:
        print(f"❌ Создание ConversationCommand - ОШИБКА: {str(e)}")

    print(f"\n📊 Базовая функциональность: {success_count}/{total_count} ОК")
    return success_count == total_count

def main():
    print("🚀 БЫСТРЫЙ ТЕСТ AI-БОТА")
    print("=" * 50)

    start_time = time.time()

    # 1. Синтаксис
    syntax_ok = test_syntax()

    # 2. Импорты
    imports_ok = test_imports()

    # 3. Базовая функциональность
    basic_ok = test_basic_functionality()

    # Итоги
    total_time = time.time() - start_time
    all_ok = syntax_ok and imports_ok and basic_ok

    print("\n" + "=" * 50)
    print("📈 РЕЗУЛЬТАТЫ БЫСТРОГО ТЕСТА")
    print(f"⏱️  Общее время: {total_time:.2f} сек")
    print(f"✅ Синтаксис: {'ПРОЙДЕН' if syntax_ok else 'ПРОВАЛ'}")
    print(f"✅ Импорты: {'ПРОЙДЕН' if imports_ok else 'ПРОВАЛ'}")
    print(f"✅ Базовая функциональность: {'ПРОЙДЕН' if basic_ok else 'ПРОВАЛ'}")
    print(f"🏆 ОБЩИЙ РЕЗУЛЬТАТ: {'ВСЕ ТЕСТЫ ПРОЙДЕНЫ' if all_ok else 'ЕСТЬ ПРОБЛЕМЫ'}")

    return 0 if all_ok else 1

if __name__ == "__main__":
    sys.exit(main())