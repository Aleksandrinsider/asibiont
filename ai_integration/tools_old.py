# Simplified TOOLS for natural AI interaction

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": "Создать новую задачу. Используй когда пользователь хочет запланировать что-то с указанием времени.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Краткое название задачи (2-5 слов)",
                    },
                    "description": {
                        "type": "string",
                        "description": "Подробности задачи (опционально)",
                    },
                    "reminder_time": {
                        "type": "string",
                        "description": "Время напоминания в формате YYYY-MM-DD HH:MM",
                    },
                },
                "required": ["title", "reminder_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Завершить задачу. Вызывай когда пользователь сообщает о выполнении задачи.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_title": {
                        "type": "string",
                        "description": "Название выполненной задачи",
                    },
                    "completion_note": {
                        "type": "string",
                        "description": "Заметка о выполнении (опционально)",
                    },
                },
                "required": ["task_title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "Показать список задач пользователя",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_completed": {
                        "type": "boolean",
                        "description": "true для выполненных задач, false для активных",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_task",
            "description": "Изменить существующую задачу (время, название, описание)",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_title": {
                        "type": "string",
                        "description": "Название задачи для изменения",
                    },
                    "title": {
                        "type": "string",
                        "description": "Новое название (опционально)",
                    },
                    "description": {
                        "type": "string",
                        "description": "Новое описание (опционально)",
                    },
                    "reminder_time": {
                        "type": "string",
                        "description": "Новое время в формате YYYY-MM-DD HH:MM (опционально)",
                    },
                },
                "required": ["task_title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_task",
            "description": "Удалить задачу",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_title": {
                        "type": "string",
                        "description": "Название задачи для удаления",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Причина удаления (опционально)",
                    },
                },
                "required": ["task_title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_task",
            "description": "Делегировать задачу другому пользователю",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Название задачи",
                    },
                    "description": {
                        "type": "string",
                        "description": "Подробное описание",
                    },
                    "reminder_time": {
                        "type": "string",
                        "description": "Дедлайн в формате YYYY-MM-DD HH:MM",
                    },
                    "delegated_to_username": {
                        "type": "string",
                        "description": "Username получателя без @",
                    },
                    "delegation_details": {
                        "type": "string",
                        "description": "Формат результата и детали",
                    },
                },
                "required": ["title", "description", "reminder_time", "delegated_to_username"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_delegation_progress",
            "description": "Показать статус делегированных задач",
            "parameters": {
                "type": "object",
                "properties": {},
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
                "properties": {
                    "task_title": {
                        "type": "string",
                        "description": "Название задачи",
                    },
                },
                "required": ["task_title"],
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
                "properties": {
                    "task_title": {
                        "type": "string",
                        "description": "Название задачи",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Причина отказа",
                    },
                },
                "required": ["task_title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_profile",
            "description": "Обновить профиль пользователя",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "Город"},
                    "interests": {"type": "string", "description": "Интересы"},
                    "skills": {"type": "string", "description": "Навыки"},
                    "goals": {"type": "string", "description": "Цели"},
                    "company": {"type": "string", "description": "Компания"},
                    "position": {"type": "string", "description": "Должность"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_partners",
            "description": "Найти контакты по интересам",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_task_details",
            "description": "Показать детали конкретной задачи",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_title": {
                        "type": "string",
                        "description": "Название задачи",
                    },
                },
                "required": ["task_title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_alternatives",
            "description": "Предложить альтернативы при проблемах с задачей",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_title": {
                        "type": "string",
                        "description": "Название проблемной задачи",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Причина проблемы",
                    },
                },
                "required": ["task_title", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_user_memory",
            "description": "Сохранить информацию в память пользователя",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_type": {
                        "type": "string",
                        "description": "Тип информации (preference, project, contact, etc.)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Что запомнить",
                    },
                },
                "required": ["memory_type", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_trends_and_opportunities",
            "description": "Предложить тренды и возможности в определенной области",
            "parameters": {
                "type": "object",
                "properties": {
                    "focus_area": {
                        "type": "string",
                        "description": "Область интереса (career, technology, business, etc.)",
                    },
                    "num_suggestions": {
                        "type": "integer",
                        "description": "Количество предложений",
                    },
                },
                "required": ["focus_area"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "brainstorm_ideas",
            "description": "Мозговой штурм идей по теме",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Тема для мозгового штурма",
                    },
                    "context": {
                        "type": "string",
                        "description": "Дополнительный контекст",
                    },
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_all_tasks",
            "description": "Удалить все задачи (опасная операция)",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]
                    "completion_note": {
                        "type": "string",
                        "description": "Заметка о выполнении - как/что сделал пользователь (опционально, извлекается из контекста)",
                    },
                },
                "required": [],
                "additionalProperties": False
            },
            "strict": True
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
                "additionalProperties": False
            },
            "strict": True
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_task",
            "description": "Создать или делегировать задачу другому пользователю. ВАЖНО: Делегирование доступно только на тарифах Standard и Premium. Пользователи тарифа Light могут ПОЛУЧАТЬ делегированные задачи, но НЕ МОГУТ делегировать свои задачи другим. 🎯 ЛОГИКА ВЫЗОВА: 1) Если пользователь указал ВСЕ детали (результат, формат, критерии, где отправить) - ВЫЗЫВАЙ функцию СРАЗУ. 2) Если какой-то информации НЕТ - сначала УТОЧНИ у пользователя: какой результат ожидается (Excel/PDF/текст), где получить (Telegram/email/Drive), критерии выполнения. Вызывай ТОЛЬКО когда в сообщении есть @username! Если нет @mention - НЕ вызывай эту функцию. ПРИ ДЕЛЕГИРОВАНИИ СУЩЕСТВУЮЩЕЙ ЗАДАЧИ: СНАЧАЛА вызови list_tasks для получения ТОЧНОГО времени, затем используй это время в reminder_time (если пользователь не указал новое время).",
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
                "additionalProperties": False
            },
            "strict": True
        },
    },
    {
        "type": "function",
        "function": {
            "name": "accept_delegated_task",
            "description": "Принять делегированную задачу. ВАЖНО: Эта функция ВСЕГДА ДОСТУПНА для всех пользователей (включая Light). Используй её когда пользователь просит принять задачу. В ответе указывай НАЗВАНИЕ задачи, а не её ID.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "ID задачи"}},
                "required": ["task_id"],
                "additionalProperties": False
            },
            "strict": True
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reject_delegated_task",
            "description": "Отклонить делегированную задачу. ВАЖНО: Эта функция ВСЕГДА ДОСТУПНА для всех пользователей (включая Light). Используй её когда пользователь просит отклонить задачу. В ответе указывай НАЗВАНИЕ задачи, а не её ID.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "ID задачи (опционально если указан task_title)"}, "task_title": {"type": "string", "description": "Название задачи или его часть (опционально если указан task_id)"}},
                "required": [],
                "additionalProperties": False
            },
            "strict": True
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
                "additionalProperties": False
            },
            "strict": True
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_task",
            "description": "Изменить существующую задачу. ⚠️ ВЫЗЫВАЙ ТОЛЬКО при ЯВНОЙ просьбе изменить/перенести/обновить КОНКРЕТНУЮ задачу: 'измени задачу X', 'перенеси задачу X на Y', 'обнови время задачи X'. НЕ вызывай если пользователь просто называет время после вопроса 'Во сколько?' - это ответ для создания НОВОЙ задачи через add_task! Можно использовать task_id ИЛИ task_title для поиска задачи.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи (опционально если указан task_title)"},
                    "task_title": {"type": "string", "description": "Название задачи для поиска (опционально если указан task_id)"},
                    "title": {"type": "string", "description": "Новое название, опционально"},
                    "description": {"type": "string", "description": "Новое описание, опционально"},
                    "reminder_time": {
                        "type": "string",
                        "description": "Новое время напоминания в формате YYYY-MM-DD HH:MM, опционально",
                    },
                },
                "required": [],
                "additionalProperties": False
            },
            "strict": True
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
                "additionalProperties": False
            },
            "strict": True
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
                "additionalProperties": False
            },
            "strict": True
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_partners",
            "description": "Найти потенциальных людей на основе профиля пользователя",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False
            },
            "strict": True
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
                "additionalProperties": False
            },
            "strict": True
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
                "additionalProperties": False
            },
            "strict": True
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_all_tasks",
            "description": "Удалить все задачи пользователя. КРИТИЧНО: Это необратимая операция! Перед вызовом ОБЯЗАТЕЛЬНО подтверди у пользователя: 'Ты точно хочешь удалить ВСЕ задачи? Это действие нельзя отменить.' и дождись явного подтверждения.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False
            },
            "strict": True
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_subscription_payment",
            "description": "Создать платеж для оформления или продления месячной подписки",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False
            },
            "strict": True
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_subscription_status",
            "description": "Проверить статус текущей подписки пользователя",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False
            },
            "strict": True
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
                "additionalProperties": False
            },
            "strict": True
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_trends_and_opportunities",
            "description": "Предложить новые тренды, интересные направления и возможности развития на основе профиля пользователя. Используй для расширения горизонтов и предложения новых идей.",
            "parameters": {
                "type": "object",
                "properties": {
                    "focus_area": {
                        "type": "string",
                        "description": "Область фокуса: 'career' (карьера), 'personal' (личное развитие), 'business' (бизнес), 'technology' (технологии), 'health' (здоровье), 'finance' (финансы), 'education' (образование), 'auto' (авто)",
                    },
                    "num_suggestions": {
                        "type": "integer",
                        "description": "Количество предложений (по умолчанию 3)",
                        "default": 3,
                    },
                },
                "required": ["focus_area"],
                "additionalProperties": False
            },
            "strict": True
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_task",
            "description": "Делегировать задачу другому пользователю (Standard/Premium). Создает задачу и отправляет уведомление получателю. КРИТИЧНО: Используй ТОЛЬКО для передачи задач другим людям через @username, НЕ для создания обычных задач!",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "КРАТКОЕ название задачи (2-5 слов). Пример: 'Написать отчет', 'Проверить код'",
                    },
                    "description": {
                        "type": "string",
                        "description": "Подробное описание с требованиями, критериями выполнения и ожидаемым результатом. Укажи формат результата и где отправить.",
                    },
                    "reminder_time": {
                        "type": "string", 
                        "description": "Дедлайн в формате YYYY-MM-DD HH:MM. Вычисляй от current_time.",
                    },
                    "delegated_to_username": {
                        "type": "string",
                        "description": "Username получателя БЕЗ @. Пример: 'ivanov' для @ivanov",
                    },
                    "delegation_details": {
                        "type": "string",
                        "description": "Дополнительные детали: формат результата, где получить результат, особые требования",
                    },
                },
                "required": ["title", "description", "reminder_time", "delegated_to_username"],
                "additionalProperties": False
            },
            "strict": True
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_delegation_progress",
            "description": "Получить отчет о статусе делегированных задач: кто принял, кто отклонил, кто работает, результаты выполнения",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False
            },
            "strict": True
        },
    },
]
