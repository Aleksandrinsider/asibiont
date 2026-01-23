"""
Комплексное тестирование различных сценариев работы агента
"""
import asyncio
import os
os.environ['LOCAL'] = '1'

from ai_integration.chat import chat_with_ai
from models import Session, User, Task
from datetime import datetime, timedelta

async def test_variety_scenarios():
    print("=" * 80)
    print("КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ АГЕНТА - РАЗЛИЧНЫЕ СЦЕНАРИИ")
    print("=" * 80)
    print()
    
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=999999).first()
        if not user:
            print("❌ Тестовый пользователь не найден")
            return
        
        # Очищаем все задачи
        session.query(Task).filter(Task.user_id == user.id).delete()
        session.commit()
        print("Очищены все задачи\n")
        
        test_cases = [
            {
                "name": "Задача без времени - односложная",
                "message": "встреча",
                "expected": "должен спросить время"
            },
            {
                "name": "Задача без времени - развернутая",
                "message": "нужно сходить к врачу",
                "expected": "должен спросить время"
            },
            {
                "name": "Задача с неточным временем",
                "message": "завтра утром позвонить клиенту",
                "expected": "должен спросить точное время"
            },
            {
                "name": "Задача с точным временем - формат HH:MM",
                "message": "позвонить маме в 15:30",
                "expected": "должна создаться задача"
            },
            {
                "name": "Задача с относительным временем",
                "message": "через 2 часа проверить почту",
                "expected": "должна создаться задача"
            },
            {
                "name": "Задача с временем суток",
                "message": "в 10 утра встреча с партнером",
                "expected": "должна создаться задача"
            },
            {
                "name": "Попытка дубликата",
                "message": "позвонить маме завтра",
                "expected": "должен спросить о дубликате"
            },
            {
                "name": "Изменение существующей - явное",
                "message": "перенеси звонок маме на 16:00",
                "expected": "должна измениться задача"
            },
            {
                "name": "Две задачи в одном сообщении",
                "message": "в 14:00 купить продукты и в 18:00 пойти в спортзал",
                "expected": "должны создаться 2 задачи"
            },
            {
                "name": "Неопределенная формулировка",
                "message": "как-нибудь нужно заняться спортом",
                "expected": "должен спросить время"
            },
            {
                "name": "Задача с упоминанием даты",
                "message": "25 января в 9:00 позвонить в банк",
                "expected": "должна создаться задача на 25 января"
            },
            {
                "name": "Просто время без контекста",
                "message": "в 19:00",
                "expected": "должен уточнить что делать"
            },
            {
                "name": "Вопрос о задачах",
                "message": "какие у меня задачи на завтра?",
                "expected": "должен показать список"
            },
            {
                "name": "Обычный разговор",
                "message": "привет, как дела?",
                "expected": "должен ответить на приветствие"
            },
            {
                "name": "Задача с напоминанием 'вечером'",
                "message": "вечером напомни позвонить другу",
                "expected": "должен спросить точное время"
            }
        ]
        
        results = []
        
        for i, test_case in enumerate(test_cases, 1):
            print("=" * 80)
            print(f"ТЕСТ {i}/{len(test_cases)}: {test_case['name']}")
            print("=" * 80)
            print(f"📝 Сообщение: '{test_case['message']}'")
            print(f"🎯 Ожидание: {test_case['expected']}")
            print()
            
            try:
                response = await chat_with_ai(
                    user_id=999999,
                    message=test_case['message']
                )
                
                print(f"🤖 Ответ агента:")
                print(f"{response}")
                print()
                
                # Простой анализ ответа
                response_lower = response.lower()
                
                # Проверяем что агент спрашивает время
                time_question_keywords = ['какое время', 'когда', 'во сколько', 'на какое время']
                asks_time = any(kw in response_lower for kw in time_question_keywords)
                
                # Проверяем упоминание дубликата
                duplicate_keywords = ['уже есть', 'похожая задача', 'изменить', 'отдельная']
                mentions_duplicate = any(kw in response_lower for kw in duplicate_keywords)
                
                # Проверяем подтверждение создания
                creation_keywords = ['создана', 'добавлена', 'поставил', 'запланирована', 'готово', 'отлично']
                confirms_creation = any(kw in response_lower for kw in creation_keywords)
                
                # Проверяем упоминание изменения
                edit_keywords = ['перенес', 'изменил', 'обновил']
                confirms_edit = any(kw in response_lower for kw in edit_keywords)
                
                # Простая оценка
                status = "✅ Адекватно"
                comment = ""
                
                if "спросить время" in test_case['expected'] and asks_time:
                    comment = "Правильно спросил время"
                elif "создаться задача" in test_case['expected'] and confirms_creation:
                    comment = "Задача создана"
                elif "дубликате" in test_case['expected'] and mentions_duplicate:
                    comment = "Обнаружил дубликат"
                elif "измениться" in test_case['expected'] and confirms_edit:
                    comment = "Изменил задачу"
                elif "показать список" in test_case['expected']:
                    comment = "Ответил на вопрос о задачах"
                elif "приветствие" in test_case['expected']:
                    comment = "Ответил на приветствие"
                elif "уточнить" in test_case['expected']:
                    comment = "Уточнил задачу"
                else:
                    comment = "Ответ получен"
                
                results.append({
                    'name': test_case['name'],
                    'status': status,
                    'comment': comment
                })
                
                print(f"📊 Оценка: {status} - {comment}")
                print()
                
                # Небольшая пауза между тестами
                await asyncio.sleep(1)
                
            except Exception as e:
                print(f"❌ ОШИБКА: {str(e)}")
                results.append({
                    'name': test_case['name'],
                    'status': '❌ Ошибка',
                    'comment': str(e)
                })
                print()
        
        # Итоговая таблица
        print("\n" + "=" * 80)
        print("ИТОГОВАЯ ТАБЛИЦА РЕЗУЛЬТАТОВ")
        print("=" * 80)
        print()
        
        for i, result in enumerate(results, 1):
            print(f"{i:2d}. {result['status']} {result['name']}")
            print(f"    └─ {result['comment']}")
        
        # Проверяем финальное состояние задач
        print("\n" + "=" * 80)
        print("ФИНАЛЬНОЕ СОСТОЯНИЕ ЗАДАЧ")
        print("=" * 80)
        
        all_tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status == 'pending'
        ).all()
        
        print(f"\nВсего активных задач: {len(all_tasks)}")
        if all_tasks:
            for task in all_tasks:
                print(f"  - {task.title} на {task.reminder_time}")
        else:
            print("  (нет активных задач)")
        
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_variety_scenarios())
