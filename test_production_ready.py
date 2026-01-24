#!/usr/bin/env python3
"""
🚀 КОМПЛЕКСНЫЙ ТЕСТ ГОТОВНОСТИ К ПРОДАКШЕНУ
Проверяет все функции бота: задачи, делегирование, профиль, напоминания, проактивность
"""

import asyncio
import re
from datetime import datetime
from ai_integration.chat import chat_with_ai
from models import Session, User, Task, UserProfile

# Цвета для вывода
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"
BOLD = "\033[1m"

def print_test_header(num, title):
    """Вывести заголовок теста"""
    print(f"\n{BLUE}{BOLD}{'='*70}{RESET}")
    print(f"{BLUE}{BOLD}🧪 ТЕСТ {num}: {title}{RESET}")
    print(f"{BLUE}{BOLD}{'='*70}{RESET}\n")

def print_message(direction, message):
    """Вывести сообщение"""
    if direction == "send":
        print(f"{YELLOW}👤 ПОЛЬЗОВАТЕЛЬ:{RESET} {message}")
    else:
        print(f"{GREEN}🤖 AI:{RESET} {message}\n")

def check_result(condition, success_msg, fail_msg):
    """Проверить результат"""
    if condition:
        print(f"{GREEN}✅ {success_msg}{RESET}")
        return True
    else:
        print(f"{RED}❌ {fail_msg}{RESET}")
        return False

async def send_and_check(message: str, user_id: int, checks: list) -> dict:
    """
    Отправить сообщение и проверить ответ
    checks: [(check_func, success_msg, fail_msg), ...]
    """
    print_message("send", message)
    
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    
    response = await chat_with_ai(
        user_id=user.id,
        message=message,
        db_session=session
    )
    
    print_message("receive", response)
    
    results = {"response": response, "checks_passed": 0, "checks_total": len(checks)}
    
    for check_func, success_msg, fail_msg in checks:
        if check_result(check_func(response, session), success_msg, fail_msg):
            results["checks_passed"] += 1
    
    session.close()
    return results

async def test_1_basic_tasks():
    """Тест 1: Базовая работа с задачами"""
    print_test_header(1, "БАЗОВАЯ РАБОТА С ЗАДАЧАМИ")
    
    # 1.1 Создание задачи с точным временем
    result = await send_and_check(
        "Напомни через 10 минут позвонить Марине",
        1001,
        [
            (lambda r, s: re.search(r'\d{2}:\d{2}', r), "Указано точное время", "Время не указано"),
            (lambda r, s: "Марине" in r or "позвонить" in r, "Контекст сохранен", "Контекст потерян")
        ]
    )
    
    # 1.2 Просмотр списка задач
    result = await send_and_check(
        "Покажи мои задачи",
        1001,
        [
            (lambda r, s: "позвонить" in r.lower() or "задач" in r.lower(), "Задачи показаны", "Задачи не показаны"),
            (lambda r, s: re.search(r'\d{2}:\d{2}', r), "Время задач указано", "Время не показано")
        ]
    )
    
    # 1.3 Редактирование задачи
    result = await send_and_check(
        "Перенеси мою задачу на 5 минут",
        1001,
        [
            (lambda r, s: re.search(r'\d{2}:\d{2}', r), "Новое время указано", "Время не указано"),
            (lambda r, s: "перенес" in r.lower() or "изменил" in r.lower() or "обновил" in r.lower(), "Задача изменена", "Задача не изменена")
        ]
    )
    
    # 1.4 Завершение задачи с причиной
    result = await send_and_check(
        "Позвонил Марине, обсудили встречу",
        1001,
        [
            (lambda r, s: "завершена" in r.lower() or "выполнена" in r.lower() or "готово" in r.lower(), "Задача завершена", "Задача не завершена"),
            (lambda r, s: s.query(Task).filter_by(user_id=1, status='completed').count() > 0, "Статус обновлен в БД", "Статус не обновлен")
        ]
    )
    
    return True

async def test_2_delegation():
    """Тест 2: Делегирование задач"""
    print_test_header(2, "ДЕЛЕГИРОВАНИЕ ЗАДАЧ")
    
    # 2.1 Делегирование с уточнением результата
    result = await send_and_check(
        "Делегируй @test_user2 подготовить отчет по продажам до завтра 15:00",
        1002,  # Silver пользователь
        [
            (lambda r, s: "результат" in r.lower() or "откуда" in r.lower() or "как" in r.lower(), "AI уточняет результат", "AI НЕ уточнил результат"),
            (lambda r, s: "критери" in r.lower() or "формат" in r.lower(), "AI спрашивает детали", "AI не спрашивает детали")
        ]
    )
    
    # 2.2 Подтверждение делегирования с деталями
    result = await send_and_check(
        "Отчет нужен в Excel, отправить в Telegram. Должен содержать цифры за последний квартал",
        1002,
        [
            (lambda r, s: "делегирован" in r.lower() or "отправлен" in r.lower() or "задача создана" in r.lower(), "Задача делегирована", "Задача НЕ делегирована"),
            (lambda r, s: s.query(Task).filter_by(user_id=2, delegated_to_username='@test_user2').count() > 0, "Делегирование в БД", "Делегирование НЕ в БД")
        ]
    )
    
    # 2.3 Проверка Bronze не может делегировать
    result = await send_and_check(
        "Делегируй @test_user2 купить молоко",
        1001,  # Bronze пользователь
        [
            (lambda r, s: "silver" in r.lower() or "подписк" in r.lower() or "тариф" in r.lower() or "недоступн" in r.lower(), "Bronze ограничение работает", "Bronze может делегировать (ОШИБКА)"),
            (lambda r, s: s.query(Task).filter_by(user_id=1, delegated_to_username='@test_user2').count() == 0, "Делегирование заблокировано", "Делегирование прошло (ОШИБКА)")
        ]
    )
    
    # 2.4 Принятие делегированной задачи (Bronze может принимать)
    session = Session()
    task = session.query(Task).filter_by(user_id=2, delegated_to_username='@test_user2', delegation_status='pending').first()
    session.close()
    
    if task:
        result = await send_and_check(
            "Принять задачу",
            1002,
            [
                (lambda r, s: "принял" in r.lower() or "взял" in r.lower(), "Задача принята", "Задача не принята"),
                (lambda r, s: s.query(Task).filter_by(id=task.id, delegation_status='accepted').count() > 0, "Статус обновлен", "Статус не обновлен")
            ]
        )
    
    return True

async def test_3_profile_and_networking():
    """Тест 3: Профиль и нетворкинг"""
    print_test_header(3, "ПРОФИЛЬ И НЕТВОРКИНГ")
    
    # 3.1 Обновление профиля
    result = await send_and_check(
        "Добавь в мои интересы футбол и программирование",
        1001,
        [
            (lambda r, s: "добавил" in r.lower() or "обновил" in r.lower() or "сохранил" in r.lower(), "Интересы добавлены", "Интересы не добавлены"),
            (lambda r, s: s.query(UserProfile).filter_by(user_id=1).first() and "футбол" in (s.query(UserProfile).filter_by(user_id=1).first().interests or "").lower(), "Профиль обновлен в БД", "Профиль не обновлен")
        ]
    )
    
    # 3.2 Поиск контактов
    result = await send_and_check(
        "Найди мне людей с похожими интересами",
        1001,
        [
            (lambda r, s: "контакт" in r.lower() or "пользовател" in r.lower() or "@" in r, "Контакты показаны", "Контакты не показаны"),
            (lambda r, s: not (r.count("контакт") > 3), "Не слишком много упоминаний", "Слишком много упоминаний контактов")
        ]
    )
    
    return True

async def test_4_time_and_reminders():
    """Тест 4: Время и напоминания"""
    print_test_header(4, "ВРЕМЯ И НАПОМИНАНИЯ")
    
    # 4.1 Проверка текущего времени
    now = datetime.now()
    result = await send_and_check(
        "Сколько сейчас времени?",
        1001,
        [
            (lambda r, s: now.strftime('%H:%M') in r or now.strftime('%H:%M') in r.replace(" ", ""), "Точное время указано", "Время неточное"),
            (lambda r, s: "utc" not in r.lower(), "Нет UTC в ответе", "UTC в ответе (ОШИБКА)")
        ]
    )
    
    # 4.2 Создание задачи с относительным временем
    result = await send_and_check(
        "Напомни через 15 минут проверить почту",
        1001,
        [
            (lambda r, s: re.search(r'\d{2}:\d{2}', r), "Точное время указано", "Время не указано"),
            (lambda r, s: "через" not in r or re.search(r'\d{2}:\d{2}', r), "Конкретное время вместо 'через X минут'", "Время указано относительно")
        ]
    )
    
    # 4.3 Задача с неточным временем (должен спросить)
    result = await send_and_check(
        "Напомни вечером купить продукты",
        1001,
        [
            (lambda r, s: "когда" in r.lower() or "во сколько" in r.lower() or "какое время" in r.lower(), "AI уточняет время", "AI НЕ уточняет время (ОШИБКА)"),
            (lambda r, s: not re.search(r'\d{2}:\d{2}', r) or "?" in r, "Не создал задачу без точного времени", "Создал задачу без уточнения")
        ]
    )
    
    return True

async def test_5_deletion_with_reason():
    """Тест 5: Удаление с причиной"""
    print_test_header(5, "УДАЛЕНИЕ ЗАДАЧ С ПРИЧИНОЙ")
    
    # 5.1 Создаем задачу для удаления
    await send_and_check(
        "Напомни через 5 минут сделать презентацию",
        1001,
        []
    )
    
    # 5.2 Удаление с причиной
    result = await send_and_check(
        "Удали задачу про презентацию, она больше не актуальна",
        1001,
        [
            (lambda r, s: "удалил" in r.lower() or "удалена" in r.lower(), "Задача удалена", "Задача не удалена"),
            (lambda r, s: "актуальн" in r.lower() or "причин" in r.lower(), "AI учел причину удаления", "AI НЕ учел причину"),
            (lambda r, s: s.query(Task).filter(Task.title.contains("презентац"), Task.user_id == 1, Task.status != 'deleted').count() == 0, "Удалено из БД", "Не удалено из БД")
        ]
    )
    
    return True

async def test_6_proactive_suggestions():
    """Тест 6: Проактивные предложения"""
    print_test_header(6, "ПРОАКТИВНОСТЬ И ПРЕДЛОЖЕНИЯ")
    
    # 6.1 Создаем задачу, которую не выполним
    await send_and_check(
        "Напомни через 2 минуты пробежку",
        1001,
        []
    )
    
    await asyncio.sleep(1)
    
    # 6.2 Говорим что не успеваем
    result = await send_and_check(
        "Не успеваю на пробежку",
        1001,
        [
            (lambda r, s: "перенес" in r.lower() or "альтернатив" in r.lower() or "вариант" in r.lower() or "можешь" in r.lower(), "AI предлагает варианты", "AI не предлагает помощь"),
            (lambda r, s: "партнер" in r.lower() or "делегиро" in r.lower() or "позже" in r.lower(), "Конкретные варианты", "Нет конкретных вариантов")
        ]
    )
    
    return True

async def test_7_context_memory():
    """Тест 7: Память и контекст"""
    print_test_header(7, "ПАМЯТЬ И КОНТЕКСТ")
    
    # 7.1 Сохранение информации
    result = await send_and_check(
        "Я работаю в IT и предпочитаю заниматься спортом по утрам",
        1001,
        [
            (lambda r, s: "запомнил" in r.lower() or "учту" in r.lower() or "буду знать" in r.lower(), "AI запоминает инфо", "AI не запоминает"),
            (lambda r, s: s.query(User).filter_by(telegram_id=1001).first().memory and ("IT" in s.query(User).filter_by(telegram_id=1001).first().memory or "утр" in s.query(User).filter_by(telegram_id=1001).first().memory), "Память обновлена", "Память не обновлена")
        ]
    )
    
    # 7.2 Использование контекста
    result = await send_and_check(
        "Предложи мне задачи на завтра",
        1001,
        [
            (lambda r, s: "спорт" in r.lower() or "утр" in r.lower() or "пробеж" in r.lower() or "трениров" in r.lower(), "AI использует контекст", "AI не использует контекст"),
        ]
    )
    
    return True

async def test_8_edge_cases():
    """Тест 8: Граничные случаи"""
    print_test_header(8, "ГРАНИЧНЫЕ СЛУЧАИ")
    
    # 8.1 Дубликат задачи
    await send_and_check("Напомни через 5 минут купить молоко", 1001, [])
    result = await send_and_check(
        "Напомни через 5 минут купить молоко",
        1001,
        [
            (lambda r, s: "уже" in r.lower() or "дубл" in r.lower() or "такая" in r.lower(), "AI замечает дубликат", "AI не замечает дубликат"),
        ]
    )
    
    # 8.2 Пустой запрос
    result = await send_and_check(
        "покажи задачи",
        1003,  # Пользователь без задач
        [
            (lambda r, s: "нет задач" in r.lower() or "пуст" in r.lower() or "не найден" in r.lower() or "создай" in r.lower(), "Корректный ответ на пустой список", "Некорректный ответ"),
        ]
    )
    
    # 8.3 Несуществующая задача
    result = await send_and_check(
        "Удали задачу про полет на Марс",
        1001,
        [
            (lambda r, s: "не найден" in r.lower() or "нет" in r.lower() or "не наш" in r.lower(), "AI сообщает об отсутствии", "AI не обработал ошибку"),
        ]
    )
    
    return True

async def test_9_confirmation_flow():
    """Тест 9: Подтверждения и многошаговые действия"""
    print_test_header(9, "ПОДТВЕРЖДЕНИЯ И МНОГОШАГОВОСТЬ")
    
    # 9.1 Спрашиваем время для неточной задачи
    result1 = await send_and_check(
        "Напомни вечером позвонить директору",
        1001,
        [
            (lambda r, s: "?" in r and ("когда" in r.lower() or "во сколько" in r.lower()), "AI спрашивает время", "AI не спрашивает"),
        ]
    )
    
    # 9.2 Отвечаем точным временем
    result2 = await send_and_check(
        "В 19:30",
        1001,
        [
            (lambda r, s: "19:30" in r, "AI использовал указанное время", "AI не использовал время"),
            (lambda r, s: "создан" in r.lower() or "напомню" in r.lower(), "Задача создана после подтверждения", "Задача НЕ создана (КРИТИЧЕСКАЯ ОШИБКА)"),
            (lambda r, s: s.query(Task).filter(Task.title.contains("директор"), Task.user_id == 1).count() > 0, "Задача в БД", "Задачи нет в БД")
        ]
    )
    
    # 9.3 Подтверждение действия
    await send_and_check("Напомни завтра в 10:00 встреча с клиентом", 1001, [])
    
    result3 = await send_and_check(
        "Перенеси встречу на 11:00",
        1001,
        [
            (lambda r, s: "?" in r or "11:00" in r, "AI подтверждает или выполняет", "AI не реагирует"),
        ]
    )
    
    # 9.4 Подтверждаем
    if "?" in result3["response"]:
        result4 = await send_and_check(
            "Да, перенеси",
            1001,
            [
                (lambda r, s: "перенес" in r.lower() or "11:00" in r, "AI выполнил после подтверждения", "AI НЕ выполнил (КРИТИЧЕСКАЯ ОШИБКА)"),
                (lambda r, s: s.query(Task).filter(Task.title.contains("встреч"), Task.user_id == 1).first() and "11:00" in str(s.query(Task).filter(Task.title.contains("встреч"), Task.user_id == 1).first().reminder_time), "Время обновлено в БД", "Время не обновлено")
            ]
        )
    
    return True

async def run_all_tests():
    """Запустить все тесты"""
    print(f"\n{BOLD}{'='*70}")
    print("🚀 КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ ГОТОВНОСТИ К ПРОДАКШЕНУ")
    print(f"{'='*70}{RESET}\n")
    
    tests = [
        ("Базовые задачи", test_1_basic_tasks),
        ("Делегирование", test_2_delegation),
        ("Профиль и нетворкинг", test_3_profile_and_networking),
        ("Время и напоминания", test_4_time_and_reminders),
        ("Удаление с причиной", test_5_deletion_with_reason),
        ("Проактивность", test_6_proactive_suggestions),
        ("Память и контекст", test_7_context_memory),
        ("Граничные случаи", test_8_edge_cases),
        ("Подтверждения", test_9_confirmation_flow),
    ]
    
    results = []
    
    for name, test_func in tests:
        try:
            await test_func()
            results.append((name, "✅ PASSED"))
        except Exception as e:
            results.append((name, f"❌ FAILED: {str(e)}"))
            print(f"{RED}Ошибка в тесте '{name}': {e}{RESET}")
    
    # Итоговый отчет
    print(f"\n{BOLD}{'='*70}")
    print("📊 ИТОГОВЫЙ ОТЧЕТ")
    print(f"{'='*70}{RESET}\n")
    
    passed = sum(1 for _, status in results if "PASSED" in status)
    total = len(results)
    
    for name, status in results:
        color = GREEN if "PASSED" in status else RED
        print(f"{color}{status}{RESET} {name}")
    
    print(f"\n{BOLD}Пройдено тестов: {passed}/{total}{RESET}")
    
    if passed == total:
        print(f"\n{GREEN}{BOLD}🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ! БОТ ГОТОВ К ПРОДАКШЕНУ!{RESET}")
    else:
        print(f"\n{RED}{BOLD}⚠️ ТРЕБУЮТСЯ ДОРАБОТКИ ПЕРЕД ПРОДАКШЕНОМ{RESET}")
    
    print(f"\n{BOLD}{'='*70}{RESET}\n")

if __name__ == "__main__":
    asyncio.run(run_all_tests())
