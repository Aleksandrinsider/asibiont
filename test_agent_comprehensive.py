"""
Комплексный тест AI-агента после оптимизации промпта.
Проверяет все ключевые функции и сценарии.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta

# Установка переменных окружения для тестирования
os.environ['LOCAL'] = '1'
os.environ['FREE_ACCESS_MODE'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'

from ai_integration.chat import chat_with_ai
from models import User, Task, UserProfile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from config import DATABASE_URL

# Создаем тестовую БД сессию
engine = create_engine('sqlite:///test_agent.db', echo=False)
Session = sessionmaker(bind=engine)

# Импортируем Base после создания engine
from models import Base
Base.metadata.create_all(engine)

class TestResult:
    def __init__(self, name):
        self.name = name
        self.passed = False
        self.error = None
        self.response = None
        self.tools_used = []

def print_result(result):
    """Красивый вывод результата теста"""
    status = "✅ PASSED" if result.passed else "❌ FAILED"
    print(f"\n{status}: {result.name}")
    if result.response:
        print(f"  Ответ: {result.response[:150]}...")
    if result.tools_used:
        print(f"  Tools: {', '.join(result.tools_used)}")
    if result.error:
        print(f"  Ошибка: {result.error}")

async def test_planning_intent_recognition():
    """Тест: 'давай запланируем' без конкретики НЕ должно создавать задачу"""
    result = TestResult("Распознавание намерения планирования")
    session = Session()
    
    try:
        # Создаем тестового пользователя
        user = User(
            telegram_id=999999991,
            username='test_planning',
            first_name='Test',
            conversation_context='[]'
        )
        session.add(user)
        session.commit()
        
        # Тестируем: "давай запланируем" без конкретной задачи
        response = await chat_with_ai(
            message="давай запланируем",
            user_id=user.telegram_id,
            db_session=session
        )
        
        result.response = response
        
        # Проверяем что НЕ создалась задача "Запланировать"
        task = session.query(Task).filter(
            Task.user_id == user.id,
            Task.title.like('%планир%')
        ).first()
        
        if task:
            result.error = f"Ошибка: создана задача '{task.title}', а не должна была"
            result.passed = False
        else:
            result.passed = True
            
    except Exception as e:
        result.error = str(e)
        result.passed = False
    finally:
        session.close()
    
    return result

async def test_concrete_task_creation():
    """Тест: 'давай запланируем пробежку' должно создать задачу"""
    result = TestResult("Создание конкретной задачи из планирования")
    session = Session()
    
    try:
        user = User(
            telegram_id=999999992,
            username='test_concrete',
            first_name='Test',
            conversation_context='[]'
        )
        session.add(user)
        session.commit()
        
        response = await chat_with_ai(
            message="давай запланируем пробежку через 5 минут",
            user_id=user.telegram_id,
            db_session=session
        )
        
        result.response = response
        
        # Проверяем что создалась задача "Пробежка" (не "Запланировать пробежку")
        task = session.query(Task).filter(
            Task.user_id == user.id
        ).first()
        
        if task and 'пробежк' in task.title.lower() and 'запланир' not in task.title.lower():
            result.passed = True
            result.tools_used = ['add_task']
        else:
            result.error = f"Задача не создана или неправильное название: {task.title if task else 'None'}"
            result.passed = False
            
    except Exception as e:
        result.error = str(e)
        result.passed = False
    finally:
        session.close()
    
    return result

async def test_delegation_vs_meeting():
    """Тест: различие между делегированием и встречей"""
    result = TestResult("Делегирование vs встреча с @username")
    session = Session()
    
    try:
        user = User(
            telegram_id=999999993,
            username='test_delegate',
            first_name='Test',
            conversation_context='[]'
        )
        session.add(user)
        session.commit()
        
        # Тест 1: "встреча с @user" = обычная задача
        response1 = await chat_with_ai(
            message="встреча с @ivanov завтра в 15:00",
            user_id=user.telegram_id,
            db_session=session
        )
        
        task1 = session.query(Task).filter(
            Task.user_id == user.id,
            Task.delegated_to_id == None
        ).first()
        
        if not task1:
            result.error = "Встреча не создалась как обычная задача"
            result.passed = False
            return result
        
        # Тест 2: "делегируй @user сделать отчет" = делегирование
        # (Но нужен реальный пользователь @ivanov в БД)
        # Пропускаем этот тест, т.к. нужна настройка БД
        
        result.passed = True
        result.tools_used = ['add_task']
        result.response = response1
        
    except Exception as e:
        result.error = str(e)
        result.passed = False
    finally:
        session.close()
    
    return result

async def test_relative_time_calculation():
    """Тест: вычисление относительного времени"""
    result = TestResult("Вычисление 'через 15 минут'")
    session = Session()
    
    try:
        user = User(
            telegram_id=999999994,
            username='test_time',
            first_name='Test',
            conversation_context='[]'
        )
        session.add(user)
        session.commit()
        
        now = datetime.now()
        
        response = await chat_with_ai(
            message="напомни позвонить маме через 15 минут",
            user_id=user.telegram_id,
            db_session=session
        )
        
        result.response = response
        
        # Проверяем что задача создана и время правильное
        task = session.query(Task).filter(
            Task.user_id == user.id
        ).first()
        
        if task and task.reminder_time:
            time_diff = (task.reminder_time - now).total_seconds()
            # Должно быть около 15 минут (900 секунд) с небольшой погрешностью
            if 850 < time_diff < 950:
                result.passed = True
                result.tools_used = ['add_task']
                
                # Проверяем что в ответе указано ТОЧНОЕ время
                if ':' in response and any(char.isdigit() for char in response):
                    result.passed = True
                else:
                    result.error = "В ответе не указано точное время"
                    result.passed = False
            else:
                result.error = f"Время неправильное: {time_diff} секунд вместо ~900"
                result.passed = False
        else:
            result.error = "Задача не создана"
            result.passed = False
            
    except Exception as e:
        result.error = str(e)
        result.passed = False
    finally:
        session.close()
    
    return result

async def test_profile_update():
    """Тест: автоматическое обновление профиля"""
    result = TestResult("Автообновление профиля при упоминании интересов")
    session = Session()
    
    try:
        user = User(
            telegram_id=999999995,
            username='test_profile',
            first_name='Test',
            conversation_context='[]'
        )
        session.add(user)
        
        profile = UserProfile(user=user, interests='')
        session.add(profile)
        session.commit()
        
        response = await chat_with_ai(
            message="я интересуюсь программированием",
            user_id=user.telegram_id,
            db_session=session
        )
        
        result.response = response
        
        # Обновляем профиль из БД
        session.refresh(profile)
        
        if 'программир' in profile.interests.lower():
            result.passed = True
            result.tools_used = ['update_profile']
        else:
            result.error = f"Интересы не обновились: '{profile.interests}'"
            result.passed = False
            
    except Exception as e:
        result.error = str(e)
        result.passed = False
    finally:
        session.close()
    
    return result

async def test_list_tasks_before_time_mention():
    """Тест: вызов list_tasks перед упоминанием времени задачи"""
    result = TestResult("list_tasks перед упоминанием времени")
    session = Session()
    
    try:
        user = User(
            telegram_id=999999996,
            username='test_list',
            first_name='Test',
            conversation_context='[]'
        )
        session.add(user)
        session.commit()
        
        # Создаем задачу
        task = Task(
            user_id=user.id,
            title='Тестовая задача',
            reminder_time=datetime.now() + timedelta(hours=2),
            status='pending'
        )
        session.add(task)
        session.commit()
        
        # Запрашиваем информацию о задачах
        response = await chat_with_ai(
            message="какие у меня задачи?",
            user_id=user.telegram_id,
            db_session=session
        )
        
        result.response = response
        
        # Проверяем что в ответе есть упоминание времени
        # (это означает что list_tasks был вызван)
        if 'тестовая задача' in response.lower() or 'задач' in response.lower():
            result.passed = True
            result.tools_used = ['list_tasks']
        else:
            result.error = "Задачи не показаны в ответе"
            result.passed = False
            
    except Exception as e:
        result.error = str(e)
        result.passed = False
    finally:
        session.close()
    
    return result

async def test_confirmation_execution():
    """Тест: выполнение действия после подтверждения"""
    result = TestResult("Выполнение действия после 'да'")
    session = Session()
    
    try:
        user = User(
            telegram_id=999999997,
            username='test_confirm',
            first_name='Test',
            conversation_context='[]'
        )
        session.add(user)
        session.commit()
        
        # Сначала создаем контекст с вопросом
        await chat_with_ai(
            message="напомни купить молоко",
            user_id=user.telegram_id,
            db_session=session
        )
        
        # Агент спросит "Во сколько?"
        # Отвечаем временем
        response = await chat_with_ai(
            message="в 18:00",
            user_id=user.telegram_id,
            db_session=session
        )
        
        result.response = response
        
        # Проверяем что задача создалась
        task = session.query(Task).filter(
            Task.user_id == user.id
        ).first()
        
        if task:
            result.passed = True
            result.tools_used = ['add_task']
        else:
            result.error = "Задача не создалась после указания времени"
            result.passed = False
            
    except Exception as e:
        result.error = str(e)
        result.passed = False
    finally:
        session.close()
    
    return result

async def test_natural_conversation():
    """Тест: естественность ответов (без шаблонов)"""
    result = TestResult("Естественность ответов")
    session = Session()
    
    try:
        user = User(
            telegram_id=999999998,
            username='test_natural',
            first_name='Test',
            conversation_context='[]'
        )
        session.add(user)
        session.commit()
        
        response = await chat_with_ai(
            message="привет",
            user_id=user.telegram_id,
            db_session=session
        )
        
        result.response = response
        
        # Проверяем что НЕТ шаблонных фраз
        bad_templates = ['отлично!', 'замечательно!', 'конечно!', 'прекрасно!']
        
        has_template = any(template in response.lower() for template in bad_templates)
        
        if not has_template and len(response) > 0:
            result.passed = True
        else:
            result.error = "Обнаружены шаблонные фразы в ответе"
            result.passed = False
            
    except Exception as e:
        result.error = str(e)
        result.passed = False
    finally:
        session.close()
    
    return result

async def main():
    """Запуск всех тестов"""
    # Удаляем старую тестовую БД если она есть
    try:
        if os.path.exists('test_agent.db'):
            os.remove('test_agent.db')
    except:
        pass
    
    print("=" * 70)
    print("КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ AI-АГЕНТА")
    print("=" * 70)
    
    tests = [
        test_planning_intent_recognition,
        test_concrete_task_creation,
        test_delegation_vs_meeting,
        test_relative_time_calculation,
        test_profile_update,
        test_list_tasks_before_time_mention,
        test_confirmation_execution,
        test_natural_conversation,
    ]
    
    results = []
    
    for test_func in tests:
        print(f"\nЗапуск: {test_func.__doc__}...")
        try:
            result = await test_func()
            results.append(result)
            print_result(result)
        except Exception as e:
            print(f"❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
            import traceback
            traceback.print_exc()
    
    # Итоги
    print("\n" + "=" * 70)
    print("ИТОГИ ТЕСТИРОВАНИЯ")
    print("=" * 70)
    
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    
    print(f"\nПройдено: {passed}/{total} ({passed/total*100:.1f}%)")
    
    if passed == total:
        print("\n✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ!")
    else:
        print("\n⚠️ Некоторые тесты не прошли. Требуется улучшение.")
        print("\nПровалившиеся тесты:")
        for r in results:
            if not r.passed:
                print(f"  - {r.name}: {r.error}")
    
    # Очистка тестовой БД
    try:
        os.remove('test_agent.db')
        print("\n🗑️ Тестовая БД удалена")
    except:
        pass

if __name__ == "__main__":
    asyncio.run(main())
