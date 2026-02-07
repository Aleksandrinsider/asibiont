import asyncio
import sys
import os
import logging
from datetime import datetime, timedelta

logging.basicConfig(
    level=logging.WARNING,  # Только важные сообщения
    format='%(message)s'
)

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from models import Session, User, Task, UserProfile, Base, engine
from ai_integration.autonomous_agent import chat_with_ai

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def print_header(text):
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'='*60}")
    print(f"{text}")
    print(f"{'='*60}{Colors.RESET}\n")

def print_test(num, total, text):
    print(f"{Colors.BLUE}[ТЕСТ {num}/{total}]{Colors.RESET} {text}")

def print_success(text):
    print(f"{Colors.GREEN}✅ УСПЕХ:{Colors.RESET} {text}")

def print_fail(text):
    print(f"{Colors.RED}❌ ПРОВАЛ:{Colors.RESET} {text}")

def print_warning(text):
    print(f"{Colors.YELLOW}⚠️  ВНИМАНИЕ:{Colors.RESET} {text}")

async def test_command(session, user_id, message, expected_behavior, check_func=None):
    """
    Универсальная функция тестирования команды
    
    Args:
        session: DB session
        user_id: User telegram ID
        message: Сообщение для AI
        expected_behavior: Описание ожидаемого поведения
        check_func: Функция проверки результата (опционально)
    """
    print(f"  📤 Запрос: '{message}'")
    
    try:
        response = await chat_with_ai(
            message=message,
            user_id=user_id,
            db_session=session
        )
        
        response_text = response.get('response', '')
        tool_calls = response.get('tool_calls', [])
        
        # Базовая проверка - ответ не пустой
        if not response_text:
            print_fail(f"Пустой ответ от AI")
            return False
        
        print(f"  💬 Ответ: {response_text[:150]}{'...' if len(response_text) > 150 else ''}")
        
        if tool_calls:
            print(f"  🔧 Вызваны инструменты: {[tc['function']['name'] for tc in tool_calls]}")
        
        # Дополнительная проверка если есть
        if check_func:
            result = check_func(session, response, response_text, tool_calls)
            if result:
                print_success(expected_behavior)
                return True
            else:
                print_fail(f"Проверка не прошла: {expected_behavior}")
                return False
        else:
            print_success(expected_behavior)
            return True
            
    except Exception as e:
        print_fail(f"Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return False

async def run_comprehensive_tests():
    """Полный набор тестов всех команд агента"""
    
    print_header("🤖 КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ АГЕНТА")
    
    Base.metadata.create_all(engine)
    session = Session()
    
    # Создаем тестового пользователя
    user_id = 999888777
    user = session.query(User).filter_by(telegram_id=user_id).first()
    
    if not user:
        user = User(
            telegram_id=user_id,
            username='test_user',
            first_name='Тест',
            timezone='Europe/Moscow'
        )
        session.add(user)
        session.commit()
        
        profile = UserProfile(
            user_id=user.id,
            interests='программирование, спорт, чтение',
            goals='изучить Python, улучшить физическую форму',
            city='Moscow'
        )
        session.add(profile)
        session.commit()
        print(f"✅ Создан тестовый пользователь (ID: {user.id})")
    
    # Очищаем старые задачи
    session.query(Task).filter_by(user_id=user.id).delete()
    session.commit()
    print(f"🗑️  Удалены старые задачи\n")
    
    results = []
    total_tests = 20
    
    # ============================================================
    # БЛОК 1: СОЗДАНИЕ ЗАДАЧ
    # ============================================================
    print_header("1️⃣  СОЗДАНИЕ ЗАДАЧ")
    
    # Тест 1: Создание задачи с конкретным временем
    print_test(1, total_tests, "Создание задачи с конкретным временем")
    def check_task_created(s, resp, text, tools):
        s.expire_all()
        tasks = s.query(Task).filter_by(user_id=user.id, title='Позвонить клиенту').all()
        return len(tasks) > 0
    
    result = await test_command(
        session, user_id,
        "Напомни позвонить клиенту завтра в 14:00",
        "Задача создана с напоминанием на завтра 14:00",
        check_task_created
    )
    results.append(("Создание задачи с временем", result))
    print()
    
    # Тест 2: Создание задачи без времени (должен спросить)
    print_test(2, total_tests, "Создание задачи без времени (проверка запроса)")
    result = await test_command(
        session, user_id,
        "Напомни купить молоко",
        "AI спрашивает время для задачи",
        lambda s, r, t, tools: any(word in t.lower() for word in ['когда', 'время', 'какое время', 'на какое время'])
    )
    results.append(("Запрос времени для задачи", result))
    print()
    
    # Тест 3: Повторяющаяся задача
    print_test(3, total_tests, "Создание повторяющейся задачи")
    def check_recurring(s, resp, text, tools):
        s.expire_all()
        tasks = s.query(Task).filter_by(user_id=user.id, is_recurring=True).all()
        return len(tasks) > 0
    
    result = await test_command(
        session, user_id,
        "Напоминай мне каждый день в 9:00 делать зарядку",
        "Создана повторяющаяся задача",
        check_recurring
    )
    results.append(("Повторяющаяся задача", result))
    print()
    
    # ============================================================
    # БЛОК 2: ПРОСМОТР И АНАЛИЗ
    # ============================================================
    print_header("2️⃣  ПРОСМОТР И АНАЛИЗ ЗАДАЧ")
    
    # Тест 4: Список задач
    print_test(4, total_tests, "Просмотр списка задач")
    result = await test_command(
        session, user_id,
        "Покажи мои задачи",
        "Отображен список задач",
        lambda s, r, t, tools: any(tc['function']['name'] == 'list_tasks' for tc in tools) or 'задач' in t.lower()
    )
    results.append(("Список задач", result))
    print()
    
    # Тест 5: Анализ задач
    print_test(5, total_tests, "Анализ выполнения задач")
    result = await test_command(
        session, user_id,
        "Проанализируй мои задачи",
        "Выполнен анализ задач",
        lambda s, r, t, tools: 'анализ' in t.lower() or any(tc['function']['name'] == 'analyze_tasks' for tc in tools)
    )
    results.append(("Анализ задач", result))
    print()
    
    # ============================================================
    # БЛОК 3: РЕДАКТИРОВАНИЕ ЗАДАЧ
    # ============================================================
    print_header("3️⃣  РЕДАКТИРОВАНИЕ ЗАДАЧ")
    
    # Создаем задачу для редактирования
    task_to_edit = Task(
        user_id=user.id,
        title='Старое название',
        status='pending',
        reminder_time=datetime.now() + timedelta(hours=2)
    )
    session.add(task_to_edit)
    session.commit()
    user.current_task_id = task_to_edit.id
    session.commit()
    
    # Тест 6: Редактирование названия
    print_test(6, total_tests, "Редактирование названия задачи")
    result = await test_command(
        session, user_id,
        "Переименуй эту задачу в 'Новое название'",
        "Задача переименована",
        lambda s, r, t, tools: any(tc['function']['name'] == 'edit_task' for tc in tools)
    )
    results.append(("Редактирование задачи", result))
    print()
    
    # Тест 7: Перенос времени
    print_test(7, total_tests, "Перенос задачи на другое время")
    result = await test_command(
        session, user_id,
        "Перенеси задачу на послезавтра в 16:00",
        "Задача перенесена",
        lambda s, r, t, tools: any(tc['function']['name'] in ['reschedule_task', 'edit_task'] for tc in tools)
    )
    results.append(("Перенос задачи", result))
    print()
    
    # ============================================================
    # БЛОК 4: ЗАВЕРШЕНИЕ И УДАЛЕНИЕ
    # ============================================================
    print_header("4️⃣  ЗАВЕРШЕНИЕ И УДАЛЕНИЕ ЗАДАЧ")
    
    # Тест 8-11: Различные фразы завершения (из предыдущего теста)
    completion_phrases = [
        ("готово", "Короткая фраза 'готово'"),
        ("я уже сделал это", "Фраза с 'я уже'"),
        ("выполнил", "Глагол 'выполнил'"),
        ("всё, закончил", "Комплексная фраза")
    ]
    
    for idx, (phrase, desc) in enumerate(completion_phrases, start=8):
        # Создаем новую задачу для каждого теста
        task = Task(
            user_id=user.id,
            title=f'Тестовая задача {idx}',
            status='pending',
            reminder_time=datetime.now() + timedelta(hours=1)
        )
        session.add(task)
        session.commit()
        user.current_task_id = task.id
        session.commit()
        
        print_test(idx, total_tests, f"Завершение: {desc}")
        
        def check_completed(s, resp, text, tools, task_id=task.id):
            # Важно: сбрасываем кэш session чтобы получить свежие данные после commit
            s.expire_all()
            t = s.query(Task).filter_by(id=task_id).first()
            return t and t.status == 'completed'
        
        result = await test_command(
            session, user_id,
            phrase,
            f"Задача закрыта фразой '{phrase}'",
            check_completed
        )
        results.append((f"Завершение: {phrase}", result))
        print()
    
    # Тест 12: Удаление задачи
    print_test(12, total_tests, "Удаление задачи")
    task_to_delete = Task(
        user_id=user.id,
        title='Задача для удаления',
        status='pending'
    )
    session.add(task_to_delete)
    session.commit()
    task_id_to_delete = task_to_delete.id
    
    def check_deleted(s, r, t, tools):
        s.expire_all()
        return s.query(Task).filter_by(id=task_id_to_delete).first() is None or \
               any(tc['function']['name'] == 'delete_task' for tc in tools)
    
    result = await test_command(
        session, user_id,
        "Удали задачу 'Задача для удаления'",
        "Задача удалена",
        check_deleted
    )
    results.append(("Удаление задачи", result))
    print()
    
    # ============================================================
    # БЛОК 5: РАБОТА С ПРОФИЛЕМ
    # ============================================================
    print_header("5️⃣  РАБОТА С ПРОФИЛЕМ")
    
    # Тест 13: Просмотр профиля
    print_test(13, total_tests, "Просмотр профиля")
    result = await test_command(
        session, user_id,
        "Покажи мой профиль",
        "Профиль отображен",
        lambda s, r, t, tools: any(tc['function']['name'] == 'show_profile' for tc in tools) or \
                               any(word in t.lower() for word in ['программирование', 'спорт', 'moscow'])
    )
    results.append(("Просмотр профиля", result))
    print()
    
    # Тест 14: Обновление профиля
    print_test(14, total_tests, "Обновление интересов в профиле")
    result = await test_command(
        session, user_id,
        "Добавь в мои интересы 'искусственный интеллект'",
        "Профиль обновлен",
        lambda s, r, t, tools: any(tc['function']['name'] in ['update_profile', 'smart_update_profile'] for tc in tools)
    )
    results.append(("Обновление профиля", result))
    print()
    
    # ============================================================
    # БЛОК 6: СОЦИАЛЬНЫЕ ФУНКЦИИ
    # ============================================================
    print_header("6️⃣  СОЦИАЛЬНЫЕ ФУНКЦИИ")
    
    # Тест 15: Поиск партнеров
    print_test(15, total_tests, "Поиск партнеров для активности")
    result = await test_command(
        session, user_id,
        "Найди людей для совместной пробежки",
        "Поиск партнеров выполнен",
        lambda s, r, t, tools: any(tc['function']['name'] in ['find_partners', 'find_relevant_contacts_for_task'] for tc in tools)
    )
    results.append(("Поиск партнеров", result))
    print()
    
    # ============================================================
    # БЛОК 7: КОНТЕКСТНОЕ ПОНИМАНИЕ
    # ============================================================
    print_header("7️⃣  КОНТЕКСТНОЕ ПОНИМАНИЕ")
    
    # Тест 16: Референсы на задачу (её, это, эту)
    task_for_ref = Task(
        user_id=user.id,
        title='Задача для референса',
        status='pending',
        reminder_time=datetime.now() + timedelta(hours=3)
    )
    session.add(task_for_ref)
    session.commit()
    user.current_task_id = task_for_ref.id
    session.commit()
    
    print_test(16, total_tests, "Понимание местоимений (её, это)")
    result = await test_command(
        session, user_id,
        "Перенеси её на завтра",
        "Контекстная задача перенесена",
        lambda s, r, t, tools: any(tc['function']['name'] in ['reschedule_task', 'edit_task'] for tc in tools)
    )
    results.append(("Контекстные референсы", result))
    print()
    
    # Тест 17: Последующий вопрос
    print_test(17, total_tests, "Последующий вопрос о задаче")
    result = await test_command(
        session, user_id,
        "А когда она теперь запланирована?",
        "AI отвечает о времени задачи",
        lambda s, r, t, tools: any(word in t.lower() for word in ['завтра', 'время', 'запланирован'])
    )
    results.append(("Последующий вопрос", result))
    print()
    
    # ============================================================
    # БЛОК 8: ОБЩЕНИЕ БЕЗ КОМАНД
    # ============================================================
    print_header("8️⃣  ЕСТЕСТВЕННОЕ ОБЩЕНИЕ")
    
    # Тест 18: Вопрос без команды
    print_test(18, total_tests, "Обычный вопрос")
    result = await test_command(
        session, user_id,
        "Как дела?",
        "AI отвечает естественно",
        lambda s, r, t, tools: len(t) > 10 and not any(word in t.lower() for word in ['error', 'ошибка'])
    )
    results.append(("Естественное общение", result))
    print()
    
    # Тест 19: Просьба о совете
    print_test(19, total_tests, "Просьба о совете по продуктивности")
    result = await test_command(
        session, user_id,
        "Посоветуй, как лучше организовать мой день",
        "AI дает советы",
        lambda s, r, t, tools: len(t) > 50
    )
    results.append(("Совет по продуктивности", result))
    print()
    
    # ============================================================
    # БЛОК 9: ГРАНИЧНЫЕ СЛУЧАИ
    # ============================================================
    print_header("9️⃣  ГРАНИЧНЫЕ СЛУЧАИ")
    
    # Тест 20: Неоднозначный запрос
    print_test(20, total_tests, "Обработка неоднозначного запроса")
    result = await test_command(
        session, user_id,
        "Это нужно сделать",
        "AI запрашивает уточнение",
        lambda s, r, t, tools: any(word in t.lower() for word in ['что', 'какую', 'уточи', 'не понял', 'поясни'])
    )
    results.append(("Неоднозначность", result))
    print()
    
    # ============================================================
    # ИТОГИ
    # ============================================================
    session.close()
    
    print_header("📊 ИТОГОВАЯ СТАТИСТИКА")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    percentage = (passed / total * 100) if total > 0 else 0
    
    print(f"\n{Colors.BOLD}Результаты по категориям:{Colors.RESET}\n")
    
    for name, result in results:
        status = f"{Colors.GREEN}✅" if result else f"{Colors.RED}❌"
        print(f"{status} {name}{Colors.RESET}")
    
    print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
    print(f"{Colors.BOLD}ИТОГО: {passed}/{total} тестов пройдено ({percentage:.1f}%){Colors.RESET}")
    print(f"{Colors.BOLD}{'='*60}{Colors.RESET}\n")
    
    if percentage >= 90:
        print_success("Агент работает отлично! Готов к продакшену 🚀")
    elif percentage >= 75:
        print_warning("Агент работает хорошо, но есть проблемы")
    else:
        print_fail("Агент требует доработки перед продакшеном")
    
    return results

if __name__ == '__main__':
    asyncio.run(run_comprehensive_tests())
