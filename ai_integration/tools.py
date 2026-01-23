# TOOLS definition для DeepSeek API























































































































































































































































    sys.exit(0 if success else 1)    success = asyncio.run(test_agent_critical())if __name__ == "__main__":    return len(errors) == 0        print("\n" + "="*80)            print("\n🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")    if not errors and not warnings:                print(f"{i}. {warning}")        for i, warning in enumerate(warnings, 1):        print(f"\n⚠️ ПРЕДУПРЕЖДЕНИЯ ({len(warnings)}):\n")    if warnings:            print("\n✅ Критических ошибок не найдено!")    else:            print(f"{i}. {error}")        for i, error in enumerate(errors, 1):        print(f"\n❌ НАЙДЕНО {len(errors)} КРИТИЧЕСКИХ ОШИБОК:\n")    if errors:        print("="*80)    print("РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ")    print("\n" + "="*80)    # ===== РЕЗУЛЬТАТЫ =====            session.close()                    print(f"⚠️ Название осталось: '{task.title if task else 'нет задачи'}'")            warnings.append("⚠️ ПРЕДУПРЕЖДЕНИЕ: Название не изменилось")        else:            print(f"✅ Название изменено: '{task.title}'")        if task and "Алексею" in task.title or "алексею" in task.title:                task = session.query(Task).filter_by(user_id=user.id).first()        session = Session()                print(f"\nОтвет AI:\n{response5}")        response5 = await chat_with_ai("измени название на Позвонить Алексею", user_id=test_user_id)                print("Сообщение: 'измени название на Позвонить Алексею'")        print(f"Текущая задача: '{old_title}'")        old_title = tasks[0].title    if len(tasks) > 0:        print("-"*80)    print("ТЕСТ 5: Редактирование названия задачи")    print("\n" + "-"*80)    # ===== ТЕСТ 5: Редактирование title без изменения времени =====        session.close()                    print(f"⚠️ Время через {diff_minutes:.1f} минут")                warnings.append(f"⚠️ ПРЕДУПРЕЖДЕНИЕ: Время через {diff_minutes:.1f} минут, ожидалось ~30")            else:                print(f"✅ Время правильное: через {diff_minutes:.1f} минут")            if 25 <= diff_minutes <= 35:  # Допуск ±5 минут            diff_minutes = (task.reminder_time - now).total_seconds() / 60            now = datetime.now()        if task.reminder_time:                print(f"\n✅ Создана задача: '{task.title}'")        task = tasks[0]    else:        errors.append(f"❌ ОШИБКА 11: Создано {len(tasks)} дубликатов задачи!")    elif len(tasks) > 1:        print("❌ Задача не создана")        errors.append("❌ ОШИБКА 10: Агент НЕ создал задачу с 'через 30 минут'!")    if len(tasks) == 0:        tasks = session.query(Task).filter_by(user_id=user.id).all()    session = Session()    # Проверка        print(f"\nОтвет AI:\n{response4}")    response4 = await chat_with_ai("напомни через 30 минут позвонить другу", user_id=test_user_id)        print("Сообщение: 'напомни через 30 минут позвонить другу'")        session.close()    session.commit()    session.query(Task).filter_by(user_id=user.id).delete()    session = Session()    # Очистка        print("-"*80)    print("ТЕСТ 4: Относительное время 'через X минут'")    print("\n" + "-"*80)    # ===== ТЕСТ 4: "Через X минут" =====        session.close()                errors.append("❌ ОШИБКА 9: reminder_time = None!")        else:                print(f"⚠️ Время: {task_time}")                warnings.append(f"⚠️ ПРЕДУПРЕЖДЕНИЕ: Время {task_time}, ожидалось 10:00")            else:                print(f"✅ Время правильное: {task_time}")            if task_time == "10:00":            task_time = task.reminder_time.strftime("%H:%M")        if task.reminder_time:                print(f"\n✅ Создана задача: '{task.title}'")        task = tasks[0]    else:        print(f"❌ Создано {len(tasks)} задач")        errors.append(f"❌ ОШИБКА 8: Создано {len(tasks)} дубликатов задачи!")    elif len(tasks) > 1:        print("❌ Задача не создана")        errors.append("❌ ОШИБКА 7: Агент НЕ создал задачу с явным указанием времени!")    if len(tasks) == 0:        tasks = session.query(Task).filter_by(user_id=user.id).all()    session = Session()    # Проверка        print(f"\nОтвет AI:\n{response3}")    response3 = await chat_with_ai("напомни позвонить маме завтра в 10:00", user_id=test_user_id)        print("Сообщение: 'напомни позвонить маме завтра в 10:00'")        session.close()    session.commit()    session.query(Task).filter_by(user_id=user.id).delete()    session = Session()    # Очистка        print("-"*80)    print("ТЕСТ 3: Создание задачи С указанием времени сразу")    print("\n" + "-"*80)    # ===== ТЕСТ 3: Создание задачи С указанием времени сразу =====        session.close()                print("❌ reminder_time = None")            errors.append("❌ ОШИБКА 6: reminder_time = None!")        else:                print(f"❌ Неправильное время: {task_time}")                errors.append(f"❌ ОШИБКА 5: Неправильное время! Ожидалось 18:00, получено {task_time}")            else:                print(f"✅ Время правильное: {task_time}")            if task_time == "18:00":            task_time = task.reminder_time.strftime("%H:%M")        if task.reminder_time:        # Проверка времени                print(f"\n✅ Создана 1 задача: '{task.title}'")        task = tasks[0]    else:            print(f"  - '{task.title}' на {task.reminder_time}")        for task in tasks:        print(f"❌ Создано {len(tasks)} задач:")        errors.append(f"❌ ОШИБКА 4: Создано {len(tasks)} дубликатов задачи!")    elif len(tasks) > 1:        print("❌ Задача не создана")        errors.append("❌ ОШИБКА 3: Агент НЕ создал задачу после указания времени!")    if len(tasks) == 0:        tasks = session.query(Task).filter_by(user_id=user.id).all()    session = Session()    # Проверка - задача ДОЛЖНА быть создана с правильным временем        print(f"\nОтвет AI:\n{response2}")    ])        {"role": "assistant", "content": response1}        {"role": "user", "content": "напомни купить продукты"},    response2 = await chat_with_ai("в 18:00", user_id=test_user_id, context=[        print("Сообщение: 'в 18:00'")    print("-"*80)    print("ТЕСТ 2: Указание времени во втором сообщении")    print("\n" + "-"*80)    # ===== ТЕСТ 2: Указание времени во втором сообщении =====        session.close()            print(f"❌ Агент не спросил время. Ответ: {response1[:100]}...")        errors.append("❌ ОШИБКА 2: Агент НЕ спросил время!")    else:        print("✅ Агент спросил время")    if "когда" in response1.lower() or "во сколько" in response1.lower() or "какое время" in response1.lower():    # Проверка - агент ДОЛЖЕН спросить время            print("\n✅ Задача не создана (правильно)")    else:        print(f"\n❌ Создана задача: '{task.title}' на {task.reminder_time}")        task = session.query(Task).filter_by(user_id=user.id).first()        errors.append("❌ ОШИБКА 1: Агент создал задачу БЕЗ уточнения времени!")    if tasks_count > 0:        tasks_count = session.query(Task).filter_by(user_id=user.id).count()    session = Session()    # Проверка - задача НЕ должна быть создана        print(f"\nОтвет AI:\n{response1}")    response1 = await chat_with_ai("напомни купить продукты", user_id=test_user_id)        print("Сообщение: 'напомни купить продукты'")    print("-"*80)    print("ТЕСТ 1: Создание задачи БЕЗ указания времени")    print("\n" + "-"*80)    # ===== ТЕСТ 1: Создание задачи БЕЗ указания времени =====        session.close()    session.commit()    session.query(Task).filter_by(user_id=user.id).delete()    # Очистка задач            session.commit()        session.add(user)        user = User(telegram_id=test_user_id, username='test_critical', first_name='Test')    if not user:    user = session.query(User).filter_by(telegram_id=test_user_id).first()    session = Session()    # Подготовка        warnings = []    errors = []    test_user_id = 12345        print("="*80)    print("КРИТИЧЕСКИЙ ТЕСТ АГЕНТА ASI BIONT")    print("\n" + "="*80)        """Тест критических проблем агента"""async def test_agent_critical():from ai_integration.chat import chat_with_aifrom models import Session, User, Task, UserProfile# Import after env setuplogger = logging.getLogger(__name__)logging.basicConfig(level=logging.INFO)os.environ['LOCAL'] = '1'# Setupfrom datetime import datetimeimport loggingimport asyncioimport sysimport os"""Критический тест агента ASI Biont - проверка основных проблемTOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": "⚠️ КРИТИЧНО: ВЫЗЫВАЙ ТОЛЬКО ЕСЛИ ПОЛЬЗОВАТЕЛЬ УКАЗАЛ ТОЧНОЕ ВРЕМЯ! Если времени нет - НЕ ВЫЗЫВАЙ, а СПРОСИ: 'Когда тебе напомнить?'. Добавить новую задачу с обязательным временем напоминания. НЕ заполняй description если пользователь не указал явные детали! Оставляй пустым. Используй ТОЧНУЮ ТЕКУЩУЮ ДАТУ из system prompt ({{current_date}}), НЕ используй даты из твоих знаний!",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Название задачи - должно быть конкретным и содержать: действие, объект, контекст. Хорошо: 'Заказать продукты домой'. Плохо: 'Позвонить другу'",
                    },
                    "description": {
                        "type": "string",
                        "description": "ОПЦИОНАЛЬНО! Оставь ПУСТЫМ если пользователь не указал детали. Если указал - МАКСИМУМ 50 символов. Примеры: 'молоко, хлеб, яйца' или 'обсудить контракт'",
                    },
                    "reminder_time": {"type": "string", "description": "⚠️ ОБЯЗАТЕЛЬНОЕ ПОЛЕ! Время напоминания в формате YYYY-MM-DD HH:MM. ОБЯЗАТЕЛЬНО используй current_date из system prompt для вычисления даты! Например, если current_date=2026-01-11 и пользователь просит 'через 5 минут в 12:30', используй '2026-01-11 12:30', а НЕ дату из прошлого! НИКОГДА не используй пустое значение, @unknown или None - СНАЧАЛА СПРОСИ У ПОЛЬЗОВАТЕЛЯ!"},
                    "due_date": {"type": "string", "description": "Дедлайн в формате YYYY-MM-DD HH:MM, опционально"},
                },
                "required": ["title", "reminder_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "Показать список задач",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Завершить существующую задачу по ID или названию. Вызывай когда пользователь говорит что выполнил/сделал/завершил задачу. НЕ создавай новую задачу, а именно заверши существующую!",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи (опционально если указан task_title)"},
                    "task_title": {
                        "type": "string",
                        "description": "Название задачи или его часть (опционально если указан task_id)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": "Установить напоминание для задачи",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи"},
                    "reminder_time": {"type": "string", "description": "Время напоминания в формате YYYY-MM-DD HH:MM"},
                },
                "required": ["task_id", "reminder_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_user_memory",
            "description": "Сохранить информацию о пользователе в долговременную память для персонализации",
            "parameters": {
                "type": "object",
                "properties": {
                    "info": {
                        "type": "string",
                        "description": "Информация для сохранения, например предпочтения, привычки, цели",
                    }
                },
                "required": ["info"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_task",
            "description": "Создать задачу для другого пользователя. ВАЖНО: Делегирование доступно только на тарифах Серебро и Золото. Пользователи тарифа Бронза могут ПОЛУЧАТЬ делегированные задачи, но НЕ МОГУТ делегировать свои задачи другим. ОБЯЗАТЕЛЬНО ТРЕБУЙ РЕЗУЛЬТАТ: при делегировании задачи ВСЕГДА уточняй у пользователя (инициатора) КАКОЙ ровно результат или откуда получить результат - ТОЛЬКО ПОТОМ делегируй. Вызывай ТОЛЬКО когда в сообщении есть @username! Если нет @mention - НЕ вызывай эту функцию. reminder_time можно указывать в естественном формате как 'завтра в 10:00', 'до послезавтра 15:00' и т.д.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Название задачи"},
                    "description": {"type": "string", "description": "Подробное описание задачи с требованиями, критериями выполнения и ОЖИДАЕМЫМ РЕЗУЛЬТАТОМ (ОБЯЗАТЕЛЬНО)"},
                    "reminder_time": {
                        "type": "string",
                        "description": "Время дедлайна в любом удобном формате: 'завтра в 10:00', 'до послезавтра 15:00', 'сегодня в 18:00' и т.д.",
                    },
                    "delegated_to_username": {
                        "type": "string",
                        "description": "Username получателя с @ (например @username)",
                    },
                    "delegation_details": {
                        "type": "string",
                        "description": "Детали: желаемый результат, критерии выполнения, важность, где получить результат",
                    },
                },
                "required": ["title", "reminder_time", "delegated_to_username"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "accept_delegated_task",
            "description": "Принять делегированную задачу. ВАЖНО: Эта функция ВСЕГДА ДОСТУПНА для всех пользователей (включая Bronze). Используй её когда пользователь просит принять задачу. В ответе указывай НАЗВАНИЕ задачи, а не её ID.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "ID задачи"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reject_delegated_task",
            "description": "Отклонить делегированную задачу. ВАЖНО: Эта функция ВСЕГДА ДОСТУПНА для всех пользователей (включая Bronze). Используй её когда пользователь просит отклонить задачу. В ответе указывай НАЗВАНИЕ задачи, а не её ID.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "ID задачи (опционально если указан task_title)"}, "task_title": {"type": "string", "description": "Название задачи или его часть (опционально если указан task_id)"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_delegation_progress",
            "description": "Получить статус выполнения делегированной задачи для инициатора",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "ID задачи"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_delegation",
            "description": "Отменить делегирование задачи. Задача вернется к инициатору без делегирования.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "ID задачи"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_task",
            "description": "Изменить название, описание или время напоминания задачи",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи"},
                    "title": {"type": "string", "description": "Новое название, опционально"},
                    "description": {"type": "string", "description": "Новое описание, опционально"},
                    "reminder_time": {
                        "type": "string",
                        "description": "Новое время напоминания в формате YYYY-MM-DD HH:MM, опционально",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_task",
            "description": "Удалить задачу по ID или названию",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи (опционально если указан task_title)"},
                    "task_title": {
                        "type": "string",
                        "description": "Название задачи или его часть (опционально если указан task_id)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_priority",
            "description": "Установить приоритет задачи",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи"},
                    "priority": {"type": "string", "description": "Приоритет: high, medium, low"},
                },
                "required": ["task_id", "priority"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_task_details",
            "description": "Получить полную информацию о задаче",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "ID задачи"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_partners",
            "description": "Найти потенциальных людей на основе профиля пользователя",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_profile",
            "description": "Обновить профиль пользователя. ВАЖНО: передавай только ЗНАЧЕНИЯ, а НЕ текст команды! ПРАВИЛА: skills/interests/goals - добавляются к существующим (используй '-' для удаления). city/company/position/languages - заменяют старые значения. Для полной очистки передай пустую строку ''",
            "parameters": {
                "type": "object",
                "properties": {
                    "skills": {"type": "string", "description": "ТОЛЬКО названия навыков через запятую. Пример: 'python, sql' или '-старый_навык' для удаления. НЕ передавай текст типа 'добавь навык X'!"},
                    "interests": {"type": "string", "description": "ТОЛЬКО названия интересов через запятую. Пример: 'спорт, музыка' или '-спорт' для удаления. НЕ передавай текст типа 'добавь в увлечения X' или 'убери из интересов Y' - извлекай ТОЛЬКО значения!"},
                    "goals": {"type": "string", "description": "ТОЛЬКО текст цели. Пример: 'выучить английский' или '-старая цель' для удаления"},
                    "city": {"type": "string", "description": "ТОЛЬКО название города. Пример: 'Москва' (ЗАМЕНЯЕТ старое значение)"},
                    "company": {"type": "string", "description": "ТОЛЬКО название компании. Пример: 'Яндекс' (ЗАМЕНЯЕТ старое значение)"},
                    "position": {"type": "string", "description": "ТОЛЬКО название должности. Пример: 'Senior Developer' (ЗАМЕНЯЕТ старое значение)"},
                    "languages": {"type": "string", "description": "ТОЛЬКО языки. Пример: 'Русский (родной), English (B2)' (ЗАМЕНЯЕТ старое значение)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_alternatives",
            "description": "Предложить альтернативы для невыполненной задачи: перенести, разбить на части, делегировать, найти партнёра",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи"},
                    "reason": {"type": "string", "description": "Причина невыполнения (опционально)"},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_all_tasks",
            "description": "Удалить все задачи пользователя. КРИТИЧНО: Это необратимая операция! Перед вызовом ОБЯЗАТЕЛЬНО подтверди у пользователя: 'Ты точно хочешь удалить ВСЕ задачи? Это действие нельзя отменить.' и дождись явного подтверждения.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_subscription_payment",
            "description": "Создать платеж для оформления или продления месячной подписки",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_subscription_status",
            "description": "Проверить статус текущей подписки пользователя",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "brainstorm_ideas",
            "description": "Сгенерировать идеи для решения проблемы или улучшения процесса. Используй когда пользователь просит идеи, советы или brainstorming.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Тема или проблема для генерации идей",
                    },
                    "num_ideas": {
                        "type": "integer",
                        "description": "Количество идей (по умолчанию 5)",
                        "default": 5,
                    },
                },
                "required": ["topic"],
            },
        },
    },
]
