"""
Комплексное тестирование AI-агента для управления задачами
Проверяет все основные сценарии работы с задачами, делегированием, профилем
"""

import asyncio
import aiohttp
import json
from datetime import datetime, timedelta

BASE_URL = "http://localhost:8000"
TEST_USER_ID = 999888777
TEST_USER2_ID = 111222333

class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
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
    print(f"{Colors.OKCYAN}ℹ {message}{Colors.ENDC}")

def print_warning(message):
    print(f"{Colors.WARNING}⚠ {message}{Colors.ENDC}")

async def send_message(session, user_id, message, username="testuser"):
    """Отправить сообщение агенту и получить ответ"""
    url = f"{BASE_URL}/chat"
    data = {
        "telegram_id": user_id,
        "message": message,
        "username": username,
        "first_name": "Test",
        "last_name": "User"
    }
    
    try:
        async with session.post(url, json=data, timeout=aiohttp.ClientTimeout(total=30)) as response:
            if response.status == 200:
                result = await response.json()
                return result.get("reply", "")
            else:
                text = await response.text()
                print_fail(f"Ошибка HTTP {response.status}: {text[:200]}")
                return None
    except Exception as e:
        print_fail(f"Ошибка запроса: {e}")
        return None

async def test_basic_task_operations(session):
    """Тест 1: Базовые операции с задачами"""
    print_test("Базовые операции с задачами")
    
    # 1. Добавление задачи
    print_info("Шаг 1: Добавление простой задачи")
    reply = await send_message(session, TEST_USER_ID, "Добавь задачу 'Купить молоко' на завтра в 10:00")
    if reply and "добав" in reply.lower():
        print_success("Задача добавлена")
    else:
        print_fail(f"Неожиданный ответ: {reply}")
    
    await asyncio.sleep(1)
    
    # 2. Просмотр задач
    print_info("Шаг 2: Просмотр списка задач")
    reply = await send_message(session, TEST_USER_ID, "Покажи мои задачи")
    if reply and "молоко" in reply.lower():
        print_success("Задача отображается в списке")
    else:
        print_fail(f"Задача не найдена в ответе: {reply}")
    
    await asyncio.sleep(1)
    
    # 3. Редактирование через уточнение
    print_info("Шаг 3: Уточнение задачи (должен сработать edit_task через Redis)")
    reply = await send_message(session, TEST_USER_ID, "нет, не молоко, а сметану")
    if reply and ("обновил" in reply.lower() or "сметан" in reply.lower()):
        print_success("Задача обновлена через Redis контекст")
    else:
        print_warning(f"Возможно не сработал edit_task: {reply}")
    
    await asyncio.sleep(1)
    
    # 4. Проверка что задача обновилась
    print_info("Шаг 4: Проверка обновления")
    reply = await send_message(session, TEST_USER_ID, "покажи задачи")
    if reply and "сметан" in reply.lower():
        print_success("Задача успешно обновлена в БД")
    else:
        print_fail(f"Задача не обновилась: {reply}")
    
    await asyncio.sleep(1)
    
    # 5. Завершение задачи
    print_info("Шаг 5: Завершение задачи")
    reply = await send_message(session, TEST_USER_ID, "Выполнил задачу со сметаной")
    if reply and ("завершен" in reply.lower() or "как прошло" in reply.lower()):
        print_success("Задача завершена, агент спрашивает о результате")
    else:
        print_fail(f"Неожиданный ответ при завершении: {reply}")
    
    await asyncio.sleep(1)
    
    # 6. Ответ на вопрос о результате
    print_info("Шаг 6: Ответ о результате задачи")
    reply = await send_message(session, TEST_USER_ID, "Все отлично, купил свежую сметану в магазине возле дома")
    print_info(f"Ответ агента: {reply[:100]}...")

async def test_task_formulation(session):
    """Тест 2: Улучшение формулировок задач"""
    print_test("Улучшение формулировок задач")
    
    # 1. Общая задача - агент должен уточнить
    print_info("Шаг 1: Общая задача (агент должен уточнить детали)")
    reply = await send_message(session, TEST_USER_ID, "Добавь задачу позвонить другу")
    if reply and ("?" in reply or "о чём" in reply.lower() or "какой" in reply.lower()):
        print_success("Агент запрашивает уточнение")
    else:
        print_warning(f"Агент не запросил уточнение: {reply}")
    
    await asyncio.sleep(1)
    
    # 2. Уточнение
    print_info("Шаг 2: Уточнение деталей")
    reply = await send_message(session, TEST_USER_ID, "Нужно обсудить поездку на выходные")
    if reply and "добав" in reply.lower():
        print_success("Агент добавил задачу с улучшенной формулировкой")
        print_info(f"Формулировка: {reply[:150]}")
    else:
        print_fail(f"Задача не добавлена: {reply}")

async def test_delegation(session):
    """Тест 3: Делегирование задач"""
    print_test("Делегирование задач")
    
    # 1. Делегирование
    print_info("Шаг 1: Делегирование задачи другому пользователю")
    reply = await send_message(session, TEST_USER_ID, "Делегируй @testuser2 проверить отчёт до завтра")
    if reply and ("делегир" in reply.lower() or "@testuser2" in reply):
        print_success("Задача делегирована")
    else:
        print_fail(f"Делегирование не сработало: {reply}")
    
    await asyncio.sleep(1)
    
    # 2. Просмотр делегированных задач
    print_info("Шаг 2: Просмотр делегированных задач для получателя")
    reply = await send_message(session, TEST_USER2_ID, "Покажи мои задачи", username="testuser2")
    if reply and "отчёт" in reply.lower():
        print_success("Получатель видит делегированную задачу")
    else:
        print_warning(f"Задача не видна получателю: {reply}")

async def test_overdue_tasks(session):
    """Тест 4: Просроченные задачи"""
    print_test("Просроченные задачи")
    
    # 1. Создаем просроченную задачу (в прошлом)
    print_info("Шаг 1: Добавление просроченной задачи")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%d.%m.%Y 10:00")
    reply = await send_message(session, TEST_USER_ID, f"Добавь задачу 'Отправить отчёт' с напоминанием вчера в 10:00")
    if reply:
        print_success("Задача с прошедшим дедлайном добавлена")
    
    await asyncio.sleep(1)
    
    # 2. Проверяем реакцию агента
    print_info("Шаг 2: Агент должен заметить просроченную задачу")
    reply = await send_message(session, TEST_USER_ID, "Что у меня по задачам?")
    if reply and ("просроч" in reply.lower() or "⚠" in reply):
        print_success("Агент обнаружил просроченную задачу и предложил помощь")
        print_info(f"Ответ: {reply[:200]}")
    else:
        print_warning(f"Агент не указал на просроченную задачу: {reply}")

async def test_profile_and_partners(session):
    """Тест 5: Профиль и поиск партнеров"""
    print_test("Профиль и поиск партнеров")
    
    # 1. Задача с интересом - агент должен предложить добавить в профиль
    print_info("Шаг 1: Задача с интересом (агент должен предложить обновить профиль)")
    reply = await send_message(session, TEST_USER_ID, "Добавь задачу пойти на йогу завтра в 19:00")
    if reply and ("профиль" in reply.lower() or "интерес" in reply.lower()):
        print_success("Агент предложил добавить интерес в профиль")
    else:
        print_info(f"Агент не предложил обновить профиль: {reply}")
    
    await asyncio.sleep(1)
    
    # 2. Обновление профиля
    print_info("Шаг 2: Обновление профиля")
    reply = await send_message(session, TEST_USER_ID, "Да, добавь йогу в интересы")
    if reply and ("обновил" in reply.lower() or "профиль" in reply.lower()):
        print_success("Профиль обновлен")
    else:
        print_warning(f"Профиль не обновлен: {reply}")
    
    await asyncio.sleep(1)
    
    # 3. Поиск партнеров
    print_info("Шаг 3: Поиск партнеров по интересам")
    reply = await send_message(session, TEST_USER_ID, "Найди мне партнеров для йоги")
    print_info(f"Результат поиска: {reply[:200] if reply else 'Нет ответа'}")

async def test_conversation_style(session):
    """Тест 6: Стиль общения агента"""
    print_test("Стиль общения и диалоговость")
    
    questions = [
        "Привет",
        "Что ты умеешь?",
        "У меня много дел, помоги организовать",
        "Как мне лучше спланировать день?",
    ]
    
    for i, question in enumerate(questions, 1):
        print_info(f"Вопрос {i}: {question}")
        reply = await send_message(session, TEST_USER_ID, question)
        
        if reply:
            word_count = len(reply.split())
            print_info(f"Длина ответа: {word_count} слов")
            
            # Проверяем диалоговость (вопросы, предложения помощи)
            is_dialog = "?" in reply or any(word in reply.lower() for word in ["давай", "хочешь", "могу", "предлага"])
            
            if is_dialog:
                print_success(f"Диалоговый ответ ✓")
            else:
                print_warning(f"Недостаточно диалоговый")
            
            print_info(f"Ответ: {reply}\n")
        
        await asyncio.sleep(1)

async def test_memory_and_context(session):
    """Тест 7: Память пользователя и контекст"""
    print_test("Память пользователя и контекст")
    
    # 1. Сообщаем информацию о себе
    print_info("Шаг 1: Сообщаем личную информацию")
    reply = await send_message(session, TEST_USER_ID, "Я работаю менеджером в IT-компании, живу в Москве")
    print_info(f"Ответ: {reply}")
    
    await asyncio.sleep(1)
    
    # 2. Проверяем что агент запомнил
    print_info("Шаг 2: Проверка памяти агента")
    reply = await send_message(session, TEST_USER_ID, "Где я работаю?")
    if reply and ("менеджер" in reply.lower() or "it" in reply.lower()):
        print_success("Агент запомнил информацию")
    else:
        print_warning(f"Агент не использует память: {reply}")

async def check_database_integrity(session):
    """Проверка целостности данных в БД"""
    print_test("Проверка целостности БД")
    
    try:
        # Пытаемся получить задачи через API
        print_info("Проверка доступности данных через API")
        reply = await send_message(session, TEST_USER_ID, "Покажи все мои задачи")
        if reply:
            print_success("API работает корректно")
            print_info(f"Получен ответ: {reply[:150]}...")
    except Exception as e:
        print_fail(f"Ошибка при проверке БД: {e}")

async def main():
    """Главная функция тестирования"""
    print(f"\n{Colors.BOLD}{Colors.HEADER}")
    print("="*70)
    print("  КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ AI-АГЕНТА ДЛЯ УПРАВЛЕНИЯ ЗАДАЧАМИ")
    print("="*70)
    print(f"{Colors.ENDC}")
    
    print_info(f"Начало тестирования: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print_info(f"Тестовый пользователь ID: {TEST_USER_ID}")
    print_info(f"Базовый URL: {BASE_URL}\n")
    
    async with aiohttp.ClientSession() as session:
        try:
            # Проверка доступности сервера
            print_info("Проверка доступности сервера...")
            try:
                async with session.get(f"{BASE_URL}/", timeout=aiohttp.ClientTimeout(total=5)) as response:
                    if response.status == 200:
                        print_success("Сервер доступен\n")
                    else:
                        print_fail(f"Сервер вернул статус {response.status}")
                        return
            except Exception as e:
                print_fail(f"Сервер недоступен: {e}")
                print_warning("Запустите сервер командой: LOCAL=1 python main.py")
                return
            
            # Запуск тестов
            await test_basic_task_operations(session)
            await asyncio.sleep(2)
            
            await test_task_formulation(session)
            await asyncio.sleep(2)
            
            await test_delegation(session)
            await asyncio.sleep(2)
            
            await test_overdue_tasks(session)
            await asyncio.sleep(2)
            
            await test_profile_and_partners(session)
            await asyncio.sleep(2)
            
            await test_conversation_style(session)
            await asyncio.sleep(2)
            
            await test_memory_and_context(session)
            await asyncio.sleep(2)
            
            await check_database_integrity(session)
            
            # Итоги
            print(f"\n{Colors.BOLD}{Colors.OKGREEN}")
            print("="*70)
            print("  ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")
            print("="*70)
            print(f"{Colors.ENDC}")
            print_info(f"Время завершения: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
        except KeyboardInterrupt:
            print_warning("\n\nТестирование прервано пользователем")
        except Exception as e:
            print_fail(f"\n\nКритическая ошибка: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
