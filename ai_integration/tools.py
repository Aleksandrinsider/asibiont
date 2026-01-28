# Simplified TOOLS for natural AI interaction

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": "📝 Создать новую задачу с напоминанием. ВСЕГДА вызывай когда: 'напомни', 'создай', 'добавь' + время. Примеры: 'напомни купить хлеб завтра в 9' → add_task(title='Купить хлеб', reminder_time='завтра в 9:00'), 'встреча послезавтра в 14:30' → add_task(title='Встреча', reminder_time='послезавтра в 14:30'), 'проверить код завтра в 11:00' → add_task(title='Проверить код', reminder_time='завтра в 11:00')",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Краткое название задачи (2-5 слов). Извлекай суть из запроса пользователя.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Подробности задачи (опционально)",
                    },
                    "reminder_time": {
                        "type": "string",
                        "description": "Время напоминания в ЛЮБОМ формате: 'завтра в 9:00', 'через 2 часа', 'послезавтра в 14:30', '15:00', 'YYYY-MM-DD HH:MM'",
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
            "description": "✅ Завершить/закрыть задачу. ОБЯЗАТЕЛЬНО вызывай когда пользователь сообщает о выполнении: 'сделал X', 'выполнил Y', 'закончил Z', 'готово', 'завершил'. Примеры: 'сделал почту' → complete_task(task_title='почту'), 'закончил отчет' → complete_task(task_title='отчет')",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_title": {
                        "type": "string",
                        "description": "Ключевые слова из названия задачи (НЕ точное название). Примеры: 'почту' найдёт 'Проверить почту', 'отчет' найдёт 'Отправить отчет'",
                    },
                    "completion_note": {
                        "type": "string",
                        "description": "Заметка о результате выполнения (опционально)",
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
            "name": "reschedule_task",
            "description": "🔥 ОСНОВНОЙ ИНСТРУМЕНТ ДЛЯ ПЕРЕНОСА ВРЕМЕНИ ЗАДАЧ. Вызывай когда видишь 'перенеси', 'измени время'. Примеры использования: 'перенеси почту на завтра в 10:00' → reschedule_task(task_title='почта', new_time='завтра в 10:00'), 'перенеси встречу на 15:30' → reschedule_task(task_title='встречу', new_time='15:30')",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_title": {
                        "type": "string",
                        "description": "Ключевые слова для поиска задачи (НЕ точное название). Примеры: 'почта' найдёт 'Проверить почту', 'встреч' найдёт 'Встреча с командой', 'молоко' найдёт 'Купить молоко'",
                    },
                    "new_time": {
                        "type": "string",
                        "description": "Новое время в ЛЮБОМ формате: 'YYYY-MM-DD HH:MM', 'HH:MM', 'через 2 часа', 'завтра в 10:00', 'послезавтра в 14:00'",
                    },
                },
                "required": ["task_title", "new_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_task",
            "description": "✏️ Изменить существующую задачу (название, описание, время). Вызывай когда видишь 'измени', 'исправь', 'обнови' задачу. Примеры: 'измени название задачи на X', 'добавь описание к задаче Y', 'измени время задачи Z'",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_title": {
                        "type": "string",
                        "description": "Ключевые слова для поиска задачи (НЕ точное название). Примеры: 'почта' найдёт 'Проверить почту', 'встреч' найдёт 'Встреча с командой'",
                    },
                    "title": {
                        "type": "string",
                        "description": "Новое название задачи (опционально)",
                    },
                    "description": {
                        "type": "string",
                        "description": "Новое описание задачи (опционально)",
                    },
                    "reminder_time": {
                        "type": "string",
                        "description": "Новое время напоминания в ЛЮБОМ формате (опционально): 'завтра в 10:00', 'через 2 часа', '15:30'",
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
            "description": "🗑️ Удалить задачу из списка. ВСЕГДА используй когда: 'удали', 'убери', 'сотри', 'больше не нужна'. Примеры: 'удали купить хлеб', 'убери задачу про встречу', 'больше не нужна задача X'",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_title": {
                        "type": "string",
                        "description": "Название или часть названия задачи для удаления. Примеры: 'купить', 'встреча', 'хлеб'",
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
            "description": "Делегировать задачу другому пользователю. Используй когда видишь 'делегируй', 'поручи', 'передай'. Примеры: 'делегируй Ивану X', 'поручи @maria X завтра в 10:00'",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Название задачи. Примеры: 'проверить документы', 'подготовить отчет'",
                    },
                    "delegated_to_username": {
                        "type": "string",
                        "description": "Имя или username получателя (без @). Примеры: 'Иван', 'maria', 'Петров'",
                    },
                    "reminder_time": {
                        "type": "string",
                        "description": "Дедлайн. Поддерживается: 'YYYY-MM-DD HH:MM', 'через 2 часа', 'завтра в 10:00' (опционально)",
                    },
                    "description": {
                        "type": "string",
                        "description": "Подробное описание задачи (опционально)",
                    },
                    "delegation_details": {
                        "type": "string",
                        "description": "Формат результата и детали (опционально)",
                    },
                },
                "required": ["title", "delegated_to_username"],
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
            "name": "delete_all_tasks",
            "description": "Удалить все задачи (опасная операция)",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_recurring_task",
            "description": "Создать повторяющуюся задачу. Вызывай когда видишь 'каждый день', 'еженедельно', 'каждую неделю', 'повторять', 'регулярно'. Примеры: 'напоминать о зарядке каждый день в 8:00', 'проверять почту каждую неделю по понедельникам', 'звонить родителям каждое воскресенье в 18:00'",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Название задачи (без указания повторения)",
                    },
                    "description": {
                        "type": "string",
                        "description": "Подробности задачи (опционально)",
                    },
                    "recurrence_pattern": {
                        "type": "string",
                        "description": "Паттерн повторения: 'daily' (каждый день), 'weekly' (каждую неделю), 'monthly' (каждый месяц), 'yearly' (каждый год)",
                        "enum": ["daily", "weekly", "monthly", "yearly"]
                    },
                    "recurrence_interval": {
                        "type": "integer",
                        "description": "Интервал повторения (опционально, по умолчанию 1). Например: 2 для 'каждые 2 дня/недели/месяца'",
                        "default": 1
                    },
                    "first_reminder_time": {
                        "type": "string",
                        "description": "Время первого напоминания в ЛЮБОМ формате: 'завтра в 9:00', 'через 2 часа', '15:00'"
                    },
                    "recurrence_end_date": {
                        "type": "string",
                        "description": "Когда прекратить повторения (опционально): 'через месяц', 'до конца года', 'YYYY-MM-DD'"
                    }
                },
                "required": ["title", "recurrence_pattern", "first_reminder_time"],
            },
        },
    },
]