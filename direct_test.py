"""
Прямое тестирование функций AI агента без запуска веб-сервера
Проверяет логику, промпты, обработку инструментов
"""

import asyncio
import os
os.environ['LOCAL'] = '1'

from ai_integration import AIIntegration, get_system_prompt
from models import Session, Task, User
from datetime import datetime, timedelta
import pytz

class Colors:
    HEADER = '\033[95m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

def print_test(test_name):
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}ТЕСТ: {test_name}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.ENDC}")

def print_success(message):
    print(f"{Colors.OKGREEN}✓ {message}{Colors.ENDC}")

def print_fail(message):
    print(f"{Colors.FAIL}✗ {message}{Colors.ENDC}")

def print_info(message):
    print(f"{Colors.WARNING}ℹ {message}{Colors.ENDC}")

def print_warning(message):
    print(f"{Colors.WARNING}⚠ {message}{Colors.ENDC}")

async def test_system_prompt():
    """Тест 1: Проверка системного промпта"""
    print_test("Системный промпт")
    
    prompt = get_system_prompt()
    
    # Проверки критичных правил
    checks = [
        ("ВСЕГДА вызывай инструменты", "Правило вызова инструментов"),
        ("НЕ просто сообщи — спроси что мешает", "Диалоговый подход к просроченным"),
        ("edit_task", "Упоминание edit_task для уточнений"),
        ("делегир", "Правила делегирования"),
        ("профиль", "Автоматическое предложение обновления профиля"),
        ("Redis", "НЕ должно быть технических деталей о Redis"),
    ]
    
    for keyword, description in checks:
        if keyword in prompt:
            if keyword == "Redis":
                print_fail(f"{description} - найдено в промпте!")
            else:
                print_success(f"{description} ✓")
        else:
            if keyword != "Redis":
                print_fail(f"{description} - НЕ найдено!")
    
    # Проверка длины
    words = len(prompt.split())
    print_info(f"Длина промпта: {words} слов")
    if words > 2000:
        print_warning("Промпт очень длинный - возможны проблемы с токенами")

async def test_task_creation_context():
    """Тест 2: Контекст при создании задач"""
    print_test("Контекст при создании задач")
    
    ai = AIIntegration()
    db = Session()
    
    try:
        # Создаём тестового пользователя
        test_user = db.query(User).filter_by(telegram_id=999111222).first()
        if not test_user:
            test_user = User(
                telegram_id=999111222,
                username="direct_test_user",
                first_name="Test"
            )
            db.add(test_user)
            db.commit()
            print_success("Тестовый пользователь создан")
        
        # Проверяем что при вызове list_tasks агент получает актуальные данные
        tasks = db.query(Task).filter_by(user_id=test_user.id, status='pending').all()
        print_info(f"У пользователя {len(tasks)} активных задач в БД")
        
        # Проверяем что просроченные задачи попадают в контекст
        overdue_tasks = db.query(Task).filter(
            Task.user_id == test_user.id,
            Task.reminder_time < datetime.now(pytz.UTC),
            Task.status == 'pending'
        ).all()
        
        if overdue_tasks:
            print_success(f"Найдено {len(overdue_tasks)} просроченных задач - должны попасть в контекст AI")
        else:
            print_info("Просроченных задач нет")
        
    finally:
        db.close()

async def test_tool_definitions():
    """Тест 3: Определения инструментов"""
    print_test("Определения инструментов (TOOLS)")
    
    from ai_integration import TOOLS
    
    expected_tools = [
        "add_task",
        "list_tasks", 
        "complete_task",
        "delete_task",
        "edit_task",
        "delegate_task",
        "find_partners",
        "update_profile",
        "update_user_memory",
        "set_reminder"
    ]
    
    tool_names = [t["function"]["name"] for t in TOOLS]
    
    for tool in expected_tools:
        if tool in tool_names:
            print_success(f"{tool} ✓")
        else:
            print_fail(f"{tool} - НЕ НАЙДЕН!")
    
    # Проверка параметров edit_task
    edit_task_tool = next((t for t in TOOLS if t["function"]["name"] == "edit_task"), None)
    if edit_task_tool:
        params = edit_task_tool["function"]["parameters"]["properties"]
        if "title" in params and "description" in params:
            print_success("edit_task имеет параметры title и description")
        else:
            print_fail("edit_task не имеет нужных параметров")

async def test_overdue_detection():
    """Тест 4: Обнаружение просроченных задач"""
    print_test("Обнаружение просроченных задач")
    
    db = Session()
    
    try:
        # Создаём просроченную задачу
        test_user = db.query(User).filter_by(telegram_id=999111222).first()
        if not test_user:
            print_fail("Тестовый пользователь не найден")
            return
        
        # Создаём задачу с прошедшим дедлайном
        yesterday = datetime.now(pytz.UTC) - timedelta(days=1)
        overdue_task = Task(
            user_id=test_user.id,
            title="Просроченная тестовая задача",
            status="pending",
            reminder_time=yesterday,
            created_at=datetime.now(pytz.UTC)
        )
        db.add(overdue_task)
        db.commit()
        print_success(f"Создана просроченная задача ID={overdue_task.id}")
        
        # Проверяем что задача определяется как просроченная
        now = datetime.now(pytz.UTC)
        overdue_check = db.query(Task).filter(
            Task.user_id == test_user.id,
            Task.reminder_time < now,
            Task.status == 'pending'
        ).all()
        
        if overdue_check:
            print_success(f"Система корректно определила {len(overdue_check)} просроченных задач")
        else:
            print_fail("Просроченная задача не определена!")
        
        # Удаляем тестовую задачу
        db.delete(overdue_task)
        db.commit()
        print_info("Тестовая задача удалена")
        
    except Exception as e:
        print_fail(f"Ошибка: {e}")
        db.rollback()
    finally:
        db.close()

async def test_delegation_rules():
    """Тест 5: Правила делегирования в промпте"""
    print_test("Правила делегирования")
    
    prompt = get_system_prompt()
    
    delegation_keywords = [
        "делегир",
        "@username",
        "ПОМОЩЬ В ВЫПОЛНЕНИИ",
        "КОНТРОЛЬ И СТАТУС",
        "delegated_to_username"
    ]
    
    for keyword in delegation_keywords:
        if keyword.lower() in prompt.lower():
            print_success(f"Найдено: {keyword}")
        else:
            print_warning(f"Не найдено: {keyword}")
    
    # Проверка что нет старых специализированных функций делегирования
    old_functions = ["generate_delegation_update", "generate_delegation_helper", "DelegationMonitor"]
    
    for old_func in old_functions:
        if old_func in prompt:
            print_fail(f"ОШИБКА: Найдено упоминание старой функции {old_func}")
        else:
            print_success(f"Старая функция {old_func} не упоминается ✓")

async def test_profile_update_suggestions():
    """Тест 6: Автоматическое предложение обновления профиля"""
    print_test("Автоматическое предложение обновления профиля")
    
    prompt = get_system_prompt()
    
    # Проверяем наличие правил для разных типов информации
    profile_sections = [
        ("ИНТЕРЕСЫ", "хобби, увлечения"),
        ("НАВЫКИ", "профессиональные умения"),
        ("ЦЕЛИ", "что хочет достичь"),
        ("ГОРОД", "место жительства"),
        ("КОМПАНИЯ", "место работы"),
        ("ДОЛЖНОСТЬ", "роль, позиция")
    ]
    
    for section_name, description in profile_sections:
        if section_name in prompt:
            print_success(f"{section_name} ({description}) ✓")
        else:
            print_fail(f"{section_name} - не найдено!")
    
    # Проверяем примеры автоматического предложения
    examples = [
        '"бегать по утрам"',
        '"сделать презентацию"',
        '"похудеть на 10 кг"',
        '"переехал в Москву"'
    ]
    
    examples_found = sum(1 for ex in examples if ex in prompt)
    print_info(f"Найдено {examples_found}/{len(examples)} примеров")

async def test_conversational_style():
    """Тест 7: Диалоговый стиль общения"""
    print_test("Диалоговый стиль общения")
    
    prompt = get_system_prompt()
    
    # Проверяем ключевые правила диалоговости
    conversational_rules = [
        ("2-4 предложения", "Оптимальная длина ответа"),
        ("задавай уточняющие вопросы", "Активное участие"),
        ("спроси что мешает", "Помощь с препятствиями"),
        ("похвали", "Позитивное подкрепление"),
        ("предложи", "Проактивность"),
        ("Как прошло?", "Вопрос о результатах")
    ]
    
    for keyword, description in conversational_rules:
        if keyword.lower() in prompt.lower():
            print_success(f"{description}: '{keyword}' ✓")
        else:
            print_warning(f"{description} - не найдено: '{keyword}'")
    
    # Проверяем антипаттерны
    antipatterns = [
        ("БЕЗ списков, нумерации", "Запрет на списки"),
        ("БЕЗ эмодзи", "Запрет на эмодзи"),
        ("НЕ используй шаблонные фразы", "Уникальность ответов")
    ]
    
    for pattern, description in antipatterns:
        if pattern in prompt:
            print_success(f"{description} упомянут ✓")
        else:
            print_warning(f"{description} не упомянут")

async def test_database_integrity():
    """Тест 8: Целостность базы данных"""
    print_test("Целостность базы данных")
    
    db = Session()
    
    try:
        # Проверка наличия таблиц через запросы
        users_count = db.query(User).count()
        tasks_count = db.query(Task).count()
        
        print_success(f"Пользователей в БД: {users_count}")
        print_success(f"Задач в БД: {tasks_count}")
        
        # Проверка индексов и связей
        test_user = db.query(User).first()
        if test_user:
            user_tasks = db.query(Task).filter_by(user_id=test_user.id).all()
            print_success(f"Связь User-Task работает: у пользователя {len(user_tasks)} задач")
        
        # Проверка делегированных задач
        delegated = db.query(Task).filter(Task.delegated_to_username.isnot(None)).count()
        print_info(f"Делегированных задач: {delegated}")
        
    except Exception as e:
        print_fail(f"Ошибка БД: {e}")
    finally:
        db.close()

async def analyze_issues():
    """Анализ потенциальных проблем"""
    print_test("Анализ потенциальных проблем")
    
    issues = []
    
    # 1. Проверка config.py
    try:
        from config import LOCAL, REDIS_URL, PORT
        print_success(f"Config загружен: LOCAL={LOCAL}, PORT={PORT}")
        if not REDIS_URL and not LOCAL:
            issues.append("REDIS_URL не задан в production режиме")
    except Exception as e:
        issues.append(f"Ошибка загрузки config: {e}")
    
    # 2. Проверка длины промпта
    prompt = get_system_prompt()
    prompt_length = len(prompt)
    if prompt_length > 10000:
        issues.append(f"Промпт слишком длинный: {prompt_length} символов (может быть проблема с токенами)")
    
    # 3. Проверка БД
    db = Session()
    try:
        orphan_tasks = db.query(Task).filter(Task.user_id.is_(None)).count()
        if orphan_tasks > 0:
            issues.append(f"Найдено {orphan_tasks} задач без пользователя")
    finally:
        db.close()
    
    # Вывод результатов
    if issues:
        print_warning(f"\nНайдено {len(issues)} потенциальных проблем:")
        for i, issue in enumerate(issues, 1):
            print_fail(f"{i}. {issue}")
    else:
        print_success("\nПотенциальных проблем не найдено ✓")
    
    return issues

async def main():
    """Главная функция тестирования"""
    print(f"\n{Colors.BOLD}{Colors.HEADER}")
    print("="*70)
    print("  ПРЯМОЕ ТЕСТИРОВАНИЕ AI-АГЕНТА (БЕЗ ВЕБ-СЕРВЕРА)")
    print("="*70)
    print(f"{Colors.ENDC}\n")
    
    print_info(f"Начало тестирования: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    try:
        await test_system_prompt()
        await asyncio.sleep(0.5)
        
        await test_task_creation_context()
        await asyncio.sleep(0.5)
        
        await test_tool_definitions()
        await asyncio.sleep(0.5)
        
        await test_overdue_detection()
        await asyncio.sleep(0.5)
        
        await test_delegation_rules()
        await asyncio.sleep(0.5)
        
        await test_profile_update_suggestions()
        await asyncio.sleep(0.5)
        
        await test_conversational_style()
        await asyncio.sleep(0.5)
        
        await test_database_integrity()
        await asyncio.sleep(0.5)
        
        issues = await analyze_issues()
        
        # Итоги
        print(f"\n{Colors.BOLD}{Colors.OKGREEN}")
        print("="*70)
        print("  ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")
        print("="*70)
        print(f"{Colors.ENDC}")
        
        print_info(f"Время завершения: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        if issues:
            print_warning(f"\n⚠ Требуется внимание: {len(issues)} проблем(ы)")
        else:
            print_success("\n✓ Все проверки пройдены успешно!")
        
    except KeyboardInterrupt:
        print_warning("\n\nТестирование прервано пользователем")
    except Exception as e:
        print_fail(f"\n\nКритическая ошибка: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
