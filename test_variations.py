"""Тест различных формулировок команд для проверки робастности AI"""
import asyncio
import os
import logging
import pytest
from datetime import datetime

os.environ['FREE_ACCESS_MODE'] = '1'
logging.basicConfig(level=logging.WARNING)

from models import init_db, Session, User, Task
from ai_integration.chat import chat_with_ai

# Расширенные тесты с различными формулировками
TEST_CASES = {
    "add_task": [
        # Прямые команды
        "напомни позвонить маме завтра в 10",
        "создай задачу купить хлеб сегодня в 18:00",
        "добавь напоминание встреча с клиентом послезавтра в 14",
        "поставь задачу отправить отчет в понедельник в 9 утра",
        "нужно сходить в банк сегодня в обед",
        
        # Естественные формулировки
        "мне нужно не забыть позвонить врачу завтра",
        "хочу чтобы ты напомнил мне про день рождения 5 февраля",
        "надо будет проверить почту через 30 минут",
        "запиши что нужно забрать посылку сегодня до 19:00",
        "помоги не забыть про встречу в четверг в 15:00",
        
        # Через относительное время
        "напомни мне через 2 часа про звонок",
        "поставь напоминание через 15 минут",
        "создай задачу на через полчаса",
        "через 5 минут нужно выйти из дома",
        "напомни через час про обед",
        
        # С контекстом
        "у меня завтра важная встреча в 10 утра, напомни",
        "нужно успеть до конца дня подготовить презентацию",
        "сегодня вечером в 20:00 тренировка, не забудь напомнить",
    ],
    
    "complete_task": [
        # Краткие формы
        "готово",
        "сделано",
        "выполнено",
        "done",
        "завершил",
        
        # С указанием задачи
        "завершил задачу про звонок",
        "выполнил встречу с клиентом",
        "готово с презентацией",
        "закончил работу над отчетом",
        "сделал покупки",
        
        # Естественные формулировки
        "я уже позвонил маме",
        "встреча прошла успешно",
        "отчет отправлен",
        "купил молоко",
        "съездил в банк",
        
        # С эмоциями
        "ура, сделал!",
        "наконец-то закончил",
        "справился с задачей",
    ],
    
    "reschedule_task": [
        # Простые переносы
        "перенеси на завтра",
        "перенеси на час позже",
        "сдвинь на 30 минут",
        "перенеси на следующую неделю",
        "измени время на 15:00",
        
        # С причинами
        "перенеси встречу, не успеваю",
        "сдвинь на 2 часа, задерживаюсь",
        "нужно перенести на вечер",
        "перенеси пожалуйста на послезавтра",
        
        # С указанием задачи
        "перенеси звонок врачу на понедельник",
        "встречу с клиентом перенеси на 16:00",
        "перенеси последнюю задачу на завтра",
        "измени время проверки почты на через час",
        
        # Естественные формулировки
        "я не успею сегодня, давай завтра",
        "можно перенести на более позднее время?",
        "лучше сделать это через 2 часа",
    ],
    
    "list_tasks": [
        # Прямые запросы
        "покажи мои задачи",
        "что у меня запланировано",
        "список дел",
        "мои задачи",
        "что нужно сделать",
        
        # Естественные
        "что у меня сегодня",
        "напомни что я планировал",
        "какие у меня дела",
        "что в планах",
        "чем заняться",
        
        # С временем
        "что у меня на завтра",
        "покажи задачи на сегодня",
        "что запланировано на эту неделю",
        "дела на ближайшие дни",
    ],
    
    "delete_task": [
        # Прямые команды
        "удали задачу про звонок",
        "убери встречу",
        "удали последнюю задачу",
        "убери напоминание про покупки",
        
        # Естественные
        "больше не нужно про это напоминать",
        "отмени встречу",
        "забудь про эту задачу",
        "это уже не актуально",
        "не нужно про это",
        
        # С объяснением
        "удали задачу, планы изменились",
        "убери напоминание, уже не надо",
        "отмени, не получится",
    ],
    
    "delegate_task": [
        # Прямые
        "делегируй задачу Ивану",
        "передай задачу коллеге",
        "поручи это Марии",
        
        # Естественные
        "попроси Ивана это сделать",
        "пусть Мария займется этим",
        "нужно чтобы коллега помог",
    ],
    
    "update_profile": [
        # Город
        "я из Москвы",
        "живу в Санкт-Петербурге",
        "мой город Казань",
        
        # Работа
        "работаю программистом",
        "я дизайнер",
        "моя профессия маркетолог",
        
        # Интересы
        "люблю программирование и спорт",
        "увлекаюсь фотографией",
        "интересуюсь искусством",
        
        # Цели
        "хочу развивать бизнес",
        "планирую выучить английский",
        "моя цель похудеть на 10 кг",
    ],
}

@pytest.mark.skip(reason="This is a helper function, not a test")
async def test_function(func_name, test_cases, user_id, session):
    """Тестирование функции с различными формулировками"""
    print(f"\n{'='*80}")
    print(f"🧪 ТЕСТИРОВАНИЕ: {func_name.upper()}")
    print(f"{'='*80}")
    
    passed = 0
    failed = 0
    errors = []
    
    for i, test_case in enumerate(test_cases, 1):
        try:
            print(f"\n[{i}/{len(test_cases)}] 📝 Запрос: '{test_case}'")
            
            response = await chat_with_ai(
                message=test_case,
                user_id=user_id,
                db_session=session
            )
            
            tool_calls = response.get('tool_calls', [])
            
            # Проверяем что нужная функция вызвана
            function_called = None
            if tool_calls:
                first_call = tool_calls[0]
                if isinstance(first_call, dict):
                    function_called = first_call.get('function')
                elif isinstance(first_call, str):
                    try:
                        function_called = json.loads(first_call).get('function')
                    except:
                        function_called = None
            
            # Для некоторых функций допустимы альтернативы
            valid_functions = {
                'add_task': ['add_task', 'set_recurring_task'],
                'complete_task': ['complete_task'],
                'reschedule_task': ['reschedule_task'],
                'list_tasks': ['list_tasks'],
                'delete_task': ['delete_task'],
                'delegate_task': ['delegate_task', 'add_task'],  # делегирование может создавать задачу
                'update_profile': ['update_profile'],
            }
            
            expected = valid_functions.get(func_name, [func_name])
            
            if function_called in expected:
                print(f"   ✅ PASS - вызвана {function_called}")
                passed += 1
            else:
                print(f"   ❌ FAIL - ожидалась {func_name}, вызвана {function_called}")
                print(f"   Ответ AI: {response.get('response', '')[:100]}")
                failed += 1
                errors.append({
                    'test': test_case,
                    'expected': func_name,
                    'got': function_called,
                    'response': response.get('response', '')[:200]
                })
                
        except Exception as e:
            print(f"   💥 ERROR: {e}")
            failed += 1
            errors.append({
                'test': test_case,
                'expected': func_name,
                'got': 'exception',
                'error': str(e)
            })
    
    print(f"\n{'='*80}")
    print(f"📊 РЕЗУЛЬТАТЫ {func_name}:")
    print(f"   ✅ Пройдено: {passed}/{len(test_cases)}")
    print(f"   ❌ Провалено: {failed}/{len(test_cases)}")
    print(f"   📈 Успешность: {(passed/len(test_cases)*100):.1f}%")
    
    if errors:
        print(f"\n⚠️  ОШИБКИ:")
        for err in errors[:3]:  # Показываем первые 3
            print(f"   • Запрос: '{err['test']}'")
            print(f"     Ожидалось: {err['expected']}, Получено: {err.get('got')}")
            if 'response' in err:
                print(f"     AI ответ: {err['response']}")
    
    return passed, failed, errors

async def main():
    print("🚀 НАЧАЛО КОМПЛЕКСНОГО ТЕСТИРОВАНИЯ РАЗЛИЧНЫХ ФОРМУЛИРОВОК")
    print(f"Дата тестирования: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    init_db()
    session = Session()
    
    # Создаем тестового пользователя
    user = session.query(User).filter_by(telegram_id=888888).first()
    if not user:
        user = User(telegram_id=888888, username='test_variations')
        session.add(user)
        session.commit()
    
    print(f"👤 Тестовый пользователь ID: {user.id}")
    
    total_passed = 0
    total_failed = 0
    all_errors = {}
    
    # Тестируем каждую функцию
    for func_name, test_cases in TEST_CASES.items():
        passed, failed, errors = await test_function(
            func_name, test_cases, user.telegram_id, session
        )
        total_passed += passed
        total_failed += failed
        if errors:
            all_errors[func_name] = errors
    
    # Итоговый отчет
    print(f"\n{'='*80}")
    print(f"📊 ИТОГОВЫЕ РЕЗУЛЬТАТЫ КОМПЛЕКСНОГО ТЕСТИРОВАНИЯ")
    print(f"{'='*80}")
    print(f"✅ Всего пройдено: {total_passed}")
    print(f"❌ Всего провалено: {total_failed}")
    total_tests = total_passed + total_failed
    print(f"📈 Общая успешность: {(total_passed/total_tests*100):.1f}%")
    
    if all_errors:
        print(f"\n⚠️  ПРОБЛЕМНЫЕ ФУНКЦИИ:")
        for func_name, errors in all_errors.items():
            print(f"\n   🔴 {func_name}: {len(errors)} ошибок")
            # Группируем ошибки по типу
            error_types = {}
            for err in errors:
                got = err.get('got', 'unknown')
                if got not in error_types:
                    error_types[got] = []
                error_types[got].append(err['test'])
            
            for error_type, tests in error_types.items():
                print(f"      • Вызвана '{error_type}' вместо '{func_name}': {len(tests)} случаев")
                if len(tests) <= 3:
                    for test in tests:
                        print(f"        - '{test}'")
    
    session.close()
    
    print(f"\n{'='*80}")
    print("✨ ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")
    print(f"{'='*80}")

if __name__ == '__main__':
    asyncio.run(main())
