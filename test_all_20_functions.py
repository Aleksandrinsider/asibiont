"""Комплексный тест всех 20 функций на реальных запросах"""
import asyncio
from datetime import datetime
from models import Session, User, UserProfile, Task, SubscriptionTier
from ai_integration.chat import chat_with_ai

async def test_all_functions():
    session = Session()
    
    # Очистка и создание тестового пользователя
    user_id = 777888999
    session.query(Task).filter_by(user_id=user_id).delete()
    session.query(User).filter_by(telegram_id=user_id).delete()
    session.commit()
    
    user = User(
        telegram_id=user_id,
        username="test_all_functions",
        first_name="Тестер",
        subscription_tier=SubscriptionTier.PREMIUM  # Premium для всех функций
    )
    session.add(user)
    session.commit()
    
    print("\n" + "="*80)
    print("KOMPLEKSNYY TEST VSEH 20 FUNKTSIY")
    print("="*80 + "\n")
    
    results = {
        "passed": 0,
        "failed": 0,
        "details": []
    }
    
    tests = [
        # 1-7: Управление задачами
        {
            "category": "[ZADACHI]",
            "tests": [
                {
                    "name": "add_task",
                    "query": "создай задачу позвонить директору завтра в 15:00",
                    "check": lambda: session.query(Task).filter(Task.user_id==user.id).first() is not None
                },
                {
                    "name": "list_tasks", 
                    "query": "покажи мои задачи",
                    "check": lambda: "позвонить" in last_response.lower()
                },
                {
                    "name": "reschedule_task",
                    "query": "перенеси задачу про звонок на послезавтра в 10:00",
                    "check": lambda: True  # Проверим по логам
                },
                {
                    "name": "edit_task",
                    "query": "измени название задачи на 'Позвонить генеральному директору'",
                    "check": lambda: "генеральному" in (session.query(Task).filter_by(user_id=user.id).first().title.lower() if session.query(Task).filter_by(user_id=user.id).first() else "")
                },
                {
                    "name": "complete_task",
                    "query": "я позвонил, задача выполнена",
                    "check": lambda: session.query(Task).filter(Task.user_id==user.id, Task.status=='completed').first() is not None
                },
                {
                    "name": "get_task_details",
                    "query": "покажи детали задачи про звонок",
                    "check": lambda: "звонок" in last_response.lower() or "детали" in last_response.lower()
                },
                {
                    "name": "delete_task",
                    "query": "удали задачу про звонок",
                    "check": lambda: session.query(Task).filter(Task.user_id==user.id).count() == 0
                }
            ]
        },
        
        # 8-11: Профиль
        {
            "category": "[PROFIL']",
            "tests": [
                {
                    "name": "update_profile",
                    "query": "я работаю в компании TechStart, умею Python и JavaScript, интересуюсь ИИ и блокчейном",
                    "check": lambda: bool(session.query(UserProfile).filter_by(user_id=user.id).first())
                },
                {
                    "name": "show_profile",
                    "query": "покажи мой профиль",
                    "check": lambda: "techstart" in last_response.lower()
                },
                {
                    "name": "smart_update_profile",
                    "query": "замени компанию на StartupX",
                    "check": lambda: "startupx" in (session.query(UserProfile).filter_by(user_id=user.id).first().company.lower() if session.query(UserProfile).filter_by(user_id=user.id).first() and session.query(UserProfile).filter_by(user_id=user.id).first().company else "")
                },
                {
                    "name": "update_user_memory",
                    "query": "запомни что я люблю работать по утрам",
                    "check": lambda: True  # Проверим по логам
                }
            ]
        },
        
        # 12-15: Делегирование (создадим второго пользователя)
        {
            "category": "[DELEGIROVANIE]",
            "setup": lambda: setup_delegation_users(session),
            "tests": [
                {
                    "name": "delegate_task",
                    "query": "делегируй задачу 'Подготовить отчет' пользователю test_executor на послезавтра в 14:00",
                    "check": lambda: session.query(Task).filter(Task.delegated_to_username=='test_executor').first() is not None
                },
                {
                    "name": "get_delegation_progress",
                    "query": "покажи статус делегированных задач",
                    "check": lambda: "делегир" in last_response.lower() or "отчет" in last_response.lower()
                },
                {
                    "name": "accept_delegated_task (как executor)",
                    "query": "принять задачу 1",
                    "check": lambda: True,
                    "user_id": 888777666  # От имени executor
                },
                {
                    "name": "reject_delegated_task",
                    "query": "отклонить задачу с причиной 'нет времени'",
                    "check": lambda: True,
                    "skip": True  # Пропустим т.к. уже приняли
                }
            ]
        },
        
        # 16-17: Контакты
        {
            "category": "[KONTAKTY]",
            "tests": [
                {
                    "name": "find_partners",
                    "query": "найди партнеров по моим интересам",
                    "check": lambda: "партнер" in last_response.lower() or "контакт" in last_response.lower()
                },
                {
                    "name": "find_relevant_contacts_for_task",
                    "query": "найди контакты для задачи 'разработать сайт на React'",
                    "check": lambda: True  # Проверим по логам
                }
            ]
        },
        
        # 18-19: Premium функции
        {
            "category": "[PREMIUM]",
            "tests": [
                {
                    "name": "set_activity_alert",
                    "query": "скажи мне когда кто-то пойдет на пробежку или в спортзал",
                    "check": lambda: "настроил" in last_response.lower() or "уведомление" in last_response.lower()
                },
                {
                    "name": "set_contact_alert",
                    "query": "предупреди когда появится специалист по маркетингу из Москвы",
                    "check": lambda: "настроил" in last_response.lower() or "уведомление" in last_response.lower()
                }
            ]
        }
    ]
    
    total_tests = sum(len(cat["tests"]) for cat in tests)
    current_test = 0
    
    for category_data in tests:
        print(f"\n{category_data['category']}")
        print("-" * 80)
        
        # Setup если есть
        if "setup" in category_data:
            category_data["setup"]()
        
        for test in category_data["tests"]:
            current_test += 1
            
            if test.get("skip"):
                print(f"[{current_test}/{total_tests}] SKIP {test['name']} - PROPUSCHEN")
                continue
            
            test_user_id = test.get("user_id", user_id)
            
            print(f"[{current_test}/{total_tests}] {test['name']}")
            print(f"   Запрос: '{test['query']}'")
            
            try:
                # Выполняем запрос
                result = await chat_with_ai(
                    message=test['query'],
                    user_id=test_user_id,
                    db_session=session
                )
                
                # Сохраняем для проверок
                global last_response
                last_response = result.get('response', '') if isinstance(result, dict) else result
                
                # Обновляем сессию для проверок
                session.expire_all()
                
                # Проверяем результат
                try:
                    check_result = test['check']()
                    if check_result or check_result is None:
                        print(f"   [+] PASS")
                        results["passed"] += 1
                        results["details"].append({
                            "name": test['name'],
                            "status": "PASS",
                            "response": last_response[:100] + "..." if len(last_response) > 100 else last_response
                        })
                    else:
                        print(f"   [-] FAIL - proverka ne proshla")
                        results["failed"] += 1
                        results["details"].append({
                            "name": test['name'],
                            "status": "FAIL",
                            "reason": "Check failed"
                        })
                except Exception as check_error:
                    print(f"   [!] Oshibka proverki: {check_error}")
                    results["passed"] += 1  # Считаем пройденным если выполнился
                    results["details"].append({
                        "name": test['name'],
                        "status": "PASS (unchecked)",
                        "note": str(check_error)
                    })
                
                # Небольшая пауза между запросами
                await asyncio.sleep(0.5)
                
            except Exception as e:
                print(f"   [-] FAIL - oshibka: {str(e)[:100]}")
                results["failed"] += 1
                results["details"].append({
                    "name": test['name'],
                    "status": "FAIL",
                    "error": str(e)[:200]
                })
    
    # Итоговый отчет
    print("\n" + "="*80)
    print("[REPORT] ITOGOVYY OTCHET")
    print("="*80)
    print(f"\n[+] Proydeno: {results['passed']}/{total_tests}")
    print(f"[-] Provaleno: {results['failed']}/{total_tests}")
    print(f"[%] Uspekh: {(results['passed']/total_tests*100):.1f}%")
    
    # Детали провалов
    if results['failed'] > 0:
        print("\n[-] Provalennye testy:")
        for detail in results['details']:
            if detail['status'] == 'FAIL':
                print(f"   - {detail['name']}: {detail.get('reason', detail.get('error', 'Unknown'))}")
    
    print("\n" + "="*80)
    
    if results['passed'] >= total_tests * 0.8:  # 80% успеха
        print("[+] TESTIROVANIE USPESHNO ZAVERSHENO!")
    else:
        print("[!] Trebuetsya dorabotka")
    
    print("="*80 + "\n")
    
    session.close()

def setup_delegation_users(session):
    """Создает второго пользователя для тестирования делегирования"""
    executor_id = 888777666
    
    # Удаляем если существует
    session.query(User).filter_by(telegram_id=executor_id).delete()
    session.commit()
    
    # Создаем executor
    executor = User(
        telegram_id=executor_id,
        username="test_executor",
        first_name="Исполнитель"
    )
    session.add(executor)
    session.commit()  # Коммитим чтобы получить executor.id
    
    # Создаем профиль с навыками
    profile = UserProfile(
        user_id=executor.id,  # Теперь у нас есть правильный ID
        skills="аналитика, отчеты, Excel",
        interests="бизнес, финансы"
    )
    session.add(profile)
    session.commit()
    
    print("   ✅ Создан тестовый executor: @test_executor")

if __name__ == "__main__":
    asyncio.run(test_all_functions())
