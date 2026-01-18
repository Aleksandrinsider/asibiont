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
            "description": "Создать задачу для другого пользователя. ВАЖНО: Делегирование доступно только на тарифах Серебро и Золото. Пользователи тарифа Бронза могут ПОЛУЧАТЬ делегированные задачи, но НЕ МОГУТ делегировать свои задачи другим. Вызывай ТОЛЬКО когда в сообщении есть @username! Если нет @mention - НЕ вызывай эту функцию. reminder_time можно указывать в естественном формате как 'завтра в 10:00', 'до послезавтра 15:00' и т.д.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Название задачи"},
                    "description": {"type": "string", "description": "Подробное описание задачи (опционально)"},
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
                        "description": "Детали: желаемый результат, критерии выполнения, важность",
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
            "description": "Принять делегированную задачу",
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
            "description": "Отклонить делегированную задачу",
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
            "description": "Обновить профиль пользователя. ВАЖНО: По умолчанию все значения ДОБАВЛЯЮТСЯ к существующим (не заменяют). Используй префикс '-' для удаления. ДЛЯ ПОЛНОЙ ОЧИСТКИ ПРОФИЛЯ: передай пустые строки '' для всех полей (city='', company='', position='', bio='', languages='', skills='', interests='', goals=''). Например: interests='бег' - добавит к существующим, interests='-криптовалюты' - удалит из списка, interests='' - полностью очистит интересы",
            "parameters": {
                "type": "object",
                "properties": {
                    "skills": {"type": "string", "description": "Навыки (добавляются к существующим, через запятую). Для удаления используй '-навык'"},
                    "interests": {"type": "string", "description": "Интересы (добавляются к существующим, через запятую). Для удаления используй '-интерес'"},
                    "goals": {"type": "string", "description": "Цели (добавляются к существующим)"},
                    "city": {"type": "string", "description": "Город пользователя (заменяет старое значение), опционально"},
                    "current_plans": {
                        "type": "string",
                        "description": "Текущие планы или события пользователя, опционально",
                    },
                    "current_time": {
                        "type": "string",
                        "description": "Текущее время пользователя в формате HH:MM, опционально",
                    },
                    "timezone": {
                        "type": "string",
                        "description": "Часовой пояс пользователя, например 'Europe/Moscow', опционально",
                    },
                    "company": {
                        "type": "string",
                        "description": "Компания, в которой работает пользователь (заменяет старое значение), опционально",
                    },
                    "bio": {
                        "type": "string",
                        "description": "Сфера деятельности пользователя (экспертиза, консультации, области сотрудничества), заменяет старое значение, опционально",
                    },
                    "languages": {
                        "type": "string",
                        "description": "Языки пользователя (например: Русский (родной), English (C1), Español (A2)), заменяет старое значение, опционально",
                    },
                    "position": {"type": "string", "description": "Должность пользователя (заменяет старое значение), опционально"},
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
