# TOOLS definition для DeepSeek API

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": "Создать задачу. КРИТИЧНО: title должен быть КРАТКИМ (2-5 слов), извлекай ТОЛЬКО суть задачи из сообщения. ПРИМЕРЫ ПРАВИЛЬНО: 'Отправить отчёт', 'Позвонить маме', 'Пробежка'. НЕПРАВИЛЬНО: 'нужно не забыть завтра с утра отправить отчёт' (слишком длинно, это НЕ title!). ВРЕМЯ: если точное ('в 10:00', 'через 2 часа') - вычисли от current_time. Если неточное ('утром', 'вечером') - НЕ вызывай функцию, СПРОСИ точное время.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "КРАТКОЕ название (2-5 слов макс!): только действие+объект. ПРАВИЛЬНО: 'Отправить отчёт по Солар', 'Позвонить Сидорову', 'Пробежка в парке'. НЕПРАВИЛЬНО: полное предложение или вопрос пользователя!",
                    },
                    "description": {
                        "type": "string",
                        "description": "ОПЦИОНАЛЬНО. Только если пользователь явно указал детали. Оставь пустым если деталей нет. Макс 50 символов.",
                    },
                    "reminder_time": {"type": "string", "description": "ОБЯЗАТЕЛЬНО! Формат: YYYY-MM-DD HH:MM. Вычисляй от current_time. Примеры: current_time=10:00 23.01.2026 → 'через 2 часа'=2026-01-23 12:00, 'завтра в 14:00'=2026-01-24 14:00. Если время неточное ('утром', 'вечером') - НЕ вызывай функцию!"},
                    "due_date": {"type": "string", "description": "Опционально. Дедлайн: YYYY-MM-DD HH:MM"},
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
            "description": "Завершить существующую задачу по ID или названию. Вызывай когда пользователь говорит что выполнил/сделал/завершил задачу. НЕ создавай новую задачу, а именно заверши существующую! ВАЖНО: Если пользователь описывает КАК он выполнил задачу или ЧТО именно сделал - учти эту информацию в своем ответе.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи (опционально если указан task_title)"},
                    "task_title": {
                        "type": "string",
                        "description": "Название задачи или его часть (опционально если указан task_id)",
                    },
                    "completion_note": {
                        "type": "string",
                        "description": "Заметка о выполнении - как/что сделал пользователь (опционально, извлекается из контекста)",
                    },
                },
                "required": [],
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
            "description": "Создать или делегировать задачу другому пользователю. ВАЖНО: Делегирование доступно только на тарифах Серебро и Золото. Пользователи тарифа Бронза могут ПОЛУЧАТЬ делегированные задачи, но НЕ МОГУТ делегировать свои задачи другим. 🎯 ЛОГИКА ВЫЗОВА: 1) Если пользователь указал ВСЕ детали (результат, формат, критерии, где отправить) - ВЫЗЫВАЙ функцию СРАЗУ. 2) Если какой-то информации НЕТ - сначала УТОЧНИ у пользователя: какой результат ожидается (Excel/PDF/текст), где получить (Telegram/email/Drive), критерии выполнения (формат, содержание). Вызывай ТОЛЬКО когда в сообщении есть @username! Если нет @mention - НЕ вызывай эту функцию. ПРИ ДЕЛЕГИРОВАНИИ СУЩЕСТВУЮЩЕЙ ЗАДАЧИ: СНАЧАЛА вызови list_tasks для получения ТОЧНОГО времени, затем используй это время в reminder_time (если пользователь не указал новое время).",
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
            "description": "Изменить существующую задачу. ⚠️ ВЫЗЫВАЙ ТОЛЬКО при ЯВНОЙ просьбе изменить/перенести/обновить КОНКРЕТНУЮ задачу: 'измени задачу X', 'перенеси задачу X на Y', 'обнови время задачи X'. НЕ вызывай если пользователь просто называет время после вопроса 'Во сколько?' - это ответ для создания НОВОЙ задачи через add_task!",
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
            "description": "Удалить задачу по ID или названию. ВАЖНО: УЧИТЫВАЙ ПРИЧИНУ УДАЛЕНИЯ в своем ответе. Если пользователь говорит 'удали задачу X, потому что Y' - обязательно упомяни причину Y в ответе (например: 'Удалил задачу X, понял что она больше не актуальна' или 'Удалил задачу X, раз уже выполнена').",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи (опционально если указан task_title)"},
                    "task_title": {
                        "type": "string",
                        "description": "Название задачи или его часть (опционально если указан task_id)",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Причина удаления (опционально, но желательно учитывать из контекста сообщения)",
                    },
                },
                "required": [],
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
            "description": "Обновить профиль пользователя. ВАЖНО: передавай только ЗНАЧЕНИЯ, а НЕ текст команды! ПРАВИЛА: skills/interests/goals - добавляются к существующим (используй '-' для удаления одного, '' пустую строку для удаления ВСЕХ). city/company/position/languages - заменяют старые значения. ПРИМЕРЫ: interests='спорт' (добавить), interests='-спорт' (убрать), interests='' (УДАЛИТЬ ВСЕ интересы)",
            "parameters": {
                "type": "object",
                "properties": {
                    "skills": {"type": "string", "description": "ТОЛЬКО названия навыков через запятую. Пример: 'python, sql' (добавить) или '-старый_навык' (удалить один) или '' (УДАЛИТЬ ВСЕ). НЕ передавай текст типа 'добавь навык X'!"},
                    "interests": {"type": "string", "description": "ТОЛЬКО названия интересов через запятую. Пример: 'спорт, музыка' (добавить) или '-спорт' (удалить один) или '' (УДАЛИТЬ ВСЕ). НЕ передавай текст типа 'добавь в увлечения X' или 'убери из интересов Y' - извлекай ТОЛЬКО значения!"},
                    "goals": {"type": "string", "description": "ТОЛЬКО текст цели. Пример: 'выучить английский' (добавить) или '-старая цель' (удалить один) или '' (УДАЛИТЬ ВСЕ)"},
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
