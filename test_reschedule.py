import os
os.environ["FREE_ACCESS_MODE"] = "1"

import asyncio
import logging
import sys
import json
from datetime import datetime
from ai_integration.chat import chat_with_ai
from models import Session, User, Task, UserProfile, init_db, SubscriptionTier
from ai_integration.handlers import delete_all_tasks

# Настройка кодировки для Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Тестовые варианты переноса
RESCHEDULE_TEST_CASES = [
    "перенеси на 5 минут",
    "перенеси почту на 5 минут",
    "перенеси проверку почты на завтра в 10 утра",
    "отложи задачу на час",
    "отложи почту на 15 минут",
    "перенеси встречу на завтра",
    "давай перенесем на 20 минут",
    "измени время задачи на 16:00",
    "поставь задачу про почту на другое время - через 30 минут",
    "подвинь встречу на час позже",
]

async def test_reschedule_variations():
    """Тестирует различные варианты переноса задач"""
    
    # Инициализация БД
    init_db()
    
    # Создаем тестового пользователя
    test_user_id = 27
    db_session = Session()
    
    try:
        user = db_session.query(User).filter_by(telegram_id=test_user_id).first()
        if not user:
            user = User(
                telegram_id=test_user_id,
                username="test_reschedule_user",
                subscription_tier=SubscriptionTier.LIGHT
            )
            db_session.add(user)
            db_session.commit()
        
        test_user_id = user.id  # Используем реальный ID
        
        # Создаем профиль
        profile = db_session.query(UserProfile).filter_by(user_id=test_user_id).first()
        if not profile:
            profile = UserProfile(
                user_id=test_user_id,
                city="Санкт-Петербург",
                interests="спорт, программирование"
            )
            db_session.add(profile)
            db_session.commit()
        
        print("=" * 80)
        print("ТЕСТ ПЕРЕНОСА ЗАДАЧ - РАЗЛИЧНЫЕ ВАРИАНТЫ ФОРМУЛИРОВОК")
        print("=" * 80)
        print()
        
        results = []
        
        for i, test_case in enumerate(RESCHEDULE_TEST_CASES, 1):
            print(f"\n{'─' * 80}")
            print(f"📝 ТЕСТ {i}/{len(RESCHEDULE_TEST_CASES)}")
            print(f"{'─' * 80}")
            
            # Удаляем все задачи перед каждым тестом
            delete_all_tasks(user_id=test_user_id, session=db_session)
            
            # Создаем тестовую задачу
            print("\n✅ Создаю тестовую задачу: 'Проверить почту через 10 минут'")
            create_response = await chat_with_ai(
                message="Создай задачу 'Проверить почту' через 10 минут",
                user_id=test_user_id,
                db_session=db_session
            )
            print(f"🤖 Ответ: {str(create_response)[:150]}...")
            
            # Ждем немного
            await asyncio.sleep(1)
            
            # Пытаемся перенести задачу
            print(f"\n🔄 Тестовый запрос: '{test_case}'")
            
            # Перехватываем логи для анализа
            import io
            from contextlib import redirect_stdout, redirect_stderr
            
            log_capture = io.StringIO()
            
            # Временно добавляем handler для захвата логов
            handler = logging.StreamHandler(log_capture)
            handler.setLevel(logging.INFO)
            ai_logger = logging.getLogger('ai_integration.chat')
            ai_logger.addHandler(handler)
            
            try:
                reschedule_response = await chat_with_ai(
                    message=test_case,
                    user_id=test_user_id,
                    db_session=db_session
                )
                
                # Анализируем логи
                logs = log_capture.getvalue()
                
                # Ищем вызовы функций
                called_functions = []
                if "reschedule_task" in logs:
                    called_functions.append("reschedule_task")
                if "Executing add_task" in logs:
                    called_functions.append("add_task")
                
                # Определяем результат
                if "reschedule_task" in called_functions and "add_task" not in called_functions:
                    result = "✅ ПРАВИЛЬНО - reschedule_task"
                    success = True
                elif "add_task" in called_functions:
                    result = "❌ ОШИБКА - add_task (создал новую вместо переноса)"
                    success = False
                else:
                    result = "⚠️  НЕ ОПРЕДЕЛЕНО"
                    success = False
                
                print(f"📊 Результат: {result}")
                print(f"🤖 Ответ: {str(reschedule_response)[:200]}...")
                
                results.append({
                    "test_case": test_case,
                    "result": result,
                    "success": success,
                    "functions_called": called_functions,
                    "response": str(reschedule_response)[:200]
                })
                
            finally:
                ai_logger.removeHandler(handler)
            
            # Проверяем количество задач
            tasks_count = db_session.query(Task).filter(
                Task.user_id == test_user_id,
                Task.status != 'completed'
            ).count()
            if tasks_count > 1:
                print(f"⚠️  ВНИМАНИЕ: В базе {tasks_count} активных задач (должна быть 1)")
        
        # Итоговая статистика
        print("\n" + "=" * 80)
        print("📊 ИТОГОВАЯ СТАТИСТИКА")
        print("=" * 80)
        
        success_count = sum(1 for r in results if r["success"])
        total_count = len(results)
        
        print(f"\n✅ Успешных тестов: {success_count}/{total_count} ({success_count/total_count*100:.1f}%)")
        print(f"❌ Провальных тестов: {total_count - success_count}/{total_count}")
        
        print("\n📋 ДЕТАЛЬНЫЕ РЕЗУЛЬТАТЫ:")
        for i, result in enumerate(results, 1):
            status = "✅" if result["success"] else "❌"
            print(f"\n{i}. {status} '{result['test_case']}'")
            print(f"   Вызвано: {', '.join(result['functions_called']) if result['functions_called'] else 'НЕТ'}")
            print(f"   {result['result']}")
        
        # Рекомендации
        print("\n" + "=" * 80)
        print("💡 РЕКОМЕНДАЦИИ")
        print("=" * 80)
        
        failed_cases = [r for r in results if not r["success"]]
        if failed_cases:
            print("\n❌ Проблемные формулировки:")
            for r in failed_cases:
                print(f"   - '{r['test_case']}'")
            print("\n💡 Нужно улучшить промпт в tools.py для функции reschedule_task")
            print("   Добавить примеры проблемных формулировок в описание")
        else:
            print("\n✅ Все тесты прошли успешно! Функция reschedule_task работает корректно.")
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        db_session.close()

if __name__ == "__main__":
    asyncio.run(test_reschedule_variations())
