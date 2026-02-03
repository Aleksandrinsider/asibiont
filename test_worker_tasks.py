"""
ТЕСТ WORKER ЗАДАЧ
Проверяем создание и удаление фоновых задач мониторинга
"""
import os
os.environ["LOCAL"] = "1"
os.environ["FREE_ACCESS_MODE"] = "1"

import asyncio
import sys
from datetime import datetime

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

from ai_integration.chat import chat_with_ai
from models import Session, User, Task, SubscriptionTier, init_db

TEST_USER_ID = 999000444  # Уникальный ID для теста worker задач

def setup_premium_user():
    """Настройка тестового пользователя с PREMIUM подпиской"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=TEST_USER_ID).first()
        if not user:
            user = User(
                telegram_id=TEST_USER_ID,
                username="test_worker_user",
                first_name="Test Worker",
                subscription_tier=SubscriptionTier.PREMIUM
            )
            session.add(user)
        else:
            user.subscription_tier = SubscriptionTier.PREMIUM
        session.commit()
        print("✅ Тестовый пользователь с PREMIUM подпиской создан")
        return user.id
    finally:
        session.close()

def verify_db(description, check_func):
    """Проверка БД через callback"""
    session = Session()
    try:
        result = check_func(session)
        status = "✅" if result else "❌"
        print(f"  {status} БД: {description}")
        return result
    finally:
        session.close()

async def test_worker_creation():
    """Тест создания worker задачи"""
    print(f"\n{'='*70}")
    print("🔧 ТЕСТ: Создание worker задачи мониторинга золота")
    print(f"{'='*70}")

    try:
        response = await asyncio.wait_for(
            chat_with_ai("Создай фоновую задачу для мониторинга золота, если цена ниже 2000", user_id=TEST_USER_ID),
            timeout=60.0  # Увеличен до 60 секунд, чтобы соответствовать API таймауту
        )
        resp_text = response.get('response', '') if isinstance(response, dict) else str(response)
        tools_called = response.get('tools_called', []) if isinstance(response, dict) else []
        print(f"💬 Ответ: {resp_text}")
        if tools_called:
            print(f"🔨 Tools called: {tools_called}")

        # Проверяем что задача создана в БД
        return verify_db(
            "Worker задача создана",
            lambda s: s.query(Task).filter(
                Task.user_id == s.query(User).filter_by(telegram_id=TEST_USER_ID).first().id,
                Task.title.like("Worker:%")
            ).first() is not None
        )

    except asyncio.TimeoutError:
        print("❌ TIMEOUT")
        return False
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return False

async def test_worker_deletion():
    """Тест удаления worker задачи"""
    print(f"\n{'='*70}")
    print("🔧 ТЕСТ: Удаление worker задачи")
    print(f"{'='*70}")

    try:
        response = await asyncio.wait_for(
            chat_with_ai("Удали фоновую задачу", user_id=TEST_USER_ID),
            timeout=60.0  # Увеличен до 60 секунд, чтобы соответствовать API таймауту
        )
        resp_text = response.get('response', '') if isinstance(response, dict) else str(response)
        tools_called = response.get('tools_called', []) if isinstance(response, dict) else []
        print(f"💬 Ответ: {resp_text}")
        if tools_called:
            print(f"🔨 Tools called: {tools_called}")

        # Проверяем что задача удалена из БД
        return verify_db(
            "Worker задача удалена",
            lambda s: s.query(Task).filter(
                Task.user_id == s.query(User).filter_by(telegram_id=TEST_USER_ID).first().id,
                Task.title.like("Worker:%")
            ).first() is None
        )

    except asyncio.TimeoutError:
        print("❌ TIMEOUT")
        return False
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return False

async def test_worker_multiple():
    """Тест возможности создания нескольких worker задач"""
    print(f"\n{'='*70}")
    print("🔧 ТЕСТ: Возможность создания нескольких worker задач")
    print(f"{'='*70}")

    try:
        # Создаем первую задачу
        await chat_with_ai("Мониторь погоду в Москве", user_id=TEST_USER_ID)

        # Создаем вторую задачу
        response = await asyncio.wait_for(
            chat_with_ai("Мониторь акции Apple", user_id=TEST_USER_ID),
            timeout=60.0  # Увеличен до 60 секунд, чтобы соответствовать API таймауту
        )
        resp_text = response.get('response', '') if isinstance(response, dict) else str(response)
        print(f"💬 Ответ на вторую задачу: {resp_text}")

        # Проверяем что созданы обе задачи
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=TEST_USER_ID).first()
            worker_count = session.query(Task).filter(
                Task.user_id == user.id,
                Task.title.like("Worker:%")
            ).count()
            result = worker_count >= 2
            status = "✅" if result else "❌"
            print(f"  {status} БД: Создано несколько worker задач (сейчас: {worker_count})")
            return result
        finally:
            session.close()

    except asyncio.TimeoutError:
        print("❌ TIMEOUT")
        return False
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return False

async def run_worker_tests():
    """Запуск всех тестов worker задач"""
    print("="*70)
    print("🚀 ТЕСТИРОВАНИЕ WORKER ЗАДАЧ")
    print("="*70)

    # Инициализация
    init_db()
    user_db_id = setup_premium_user()

    # Очистка существующих worker задач
    session = Session()
    session.query(Task).filter(
        Task.user_id == user_db_id,
        Task.title.like("Worker:%")
    ).delete()
    session.commit()
    session.close()
    print("✅ БД очищена от worker задач\n")

    results = []

    # Тесты
    results.append(await test_worker_creation())
    results.append(await test_worker_multiple())
    results.append(await test_worker_deletion())

    # ИТОГИ
    print("\n" + "="*70)
    print("📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ WORKER ЗАДАЧ")
    print("="*70)

    passed = sum(1 for r in results if r)
    total = len(results)

    print(f"\n✅ Пройдено: {passed}/{total} ({passed*100//total}%)")

    if passed == total:
        print("🎉 ВСЕ WORKER ТЕСТЫ ПРОЙДЕНЫ!")
    else:
        print(f"⚠️ Провалено: {total - passed} тестов")

    print("="*70)

    return passed == total

if __name__ == "__main__":
    try:
        success = asyncio.run(run_worker_tests())
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)