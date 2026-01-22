# TOOLS definition для DeepSeek API

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": "Добавить новую задачу с обязательным временем напоминания. КРИТИЧНО: НЕ заполняй description если пользователь не указал явные детали! Оставляй пустым. КРИТИЧНО: используй ТОЧНУЮ ТЕКУЩУЮ ДАТУ из system prompt ({{current_date}}), НЕ используй даты из твоих знаний!",
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
                    "reminder_time": {"type": "string", "description": "Время напоминания в формате YYYY-MM-DD HH:MM. ОБЯЗАТЕЛЬНО используй current_date из system prompt для вычисления даты! Например, если current_date=2026-01-11 и пользователь просит 'через 5 минут в 12:30', используй '2026-01-11 12:30', а НЕ дату из прошлого!"},
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
            "description": "Обновить профиль пользователя на основе диалога. АНАЛИЗИРУЙ КОНТЕКСТ: 'мне интересно X' = ДОБАВИТЬ интерес, 'я больше не интересуюсь X' = УБРАТЬ интерес, 'я работаю в X' = ЗАМЕНИТЬ компанию. ПРАВИЛА: skills/interests/goals - добавляются к существующим (используй '-' для удаления). city/company/position - заменяют старые значения. Для полной очистки передай пустую строку ''",
            "parameters": {
                "type": "object",
                "properties": {
                    "skills": {"type": "string", "description": "Навыки через запятую. ДОБАВЛЯТЬ: 'программирование, python'. УБИРАТЬ: '-старый_навык'. ОЧИСТИТЬ: ''"},
                    "interests": {"type": "string", "description": "Интересы через запятую. ДОБАВЛЯТЬ: 'спорт, музыка'. УБИРАТЬ: '-старый_интерес'. ОЧИСТИТЬ: ''"},
                    "goals": {"type": "string", "description": "Цели пользователя. ДОБАВЛЯТЬ к существующим или ЗАМЕНИТЬ полностью"},
                    "city": {"type": "string", "description": "Город (ЗАМЕНЯЕТ старое значение)"},
                    "company": {"type": "string", "description": "Компания (ЗАМЕНЯЕТ старое значение)"},
                    "position": {"type": "string", "description": "Должность (ЗАМЕНЯЕТ старое значение)"},
                    "bio": {"type": "string", "description": "Описание деятельности (ЗАМЕНЯЕТ старое значение)"},
                    "languages": {"type": "string", "description": "Языки (ЗАМЕНЯЕТ старое значение)"},
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
