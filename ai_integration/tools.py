# Enhanced TOOLS with clear, specific descriptions to prevent AI hallucinations

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": "СОЗДАТЬ НОВУЮ ЗАДАЧУ С НАПОМИНАНИЕМ. Вызывай когда пользователь хочет создать задачу с временем. Ключевые слова: 'напомни', 'создай задачу', 'добавь напоминание', 'запланируй'. НЕ вызывай для завершения, изменения или просмотра задач. Примеры: 'напомни купить хлеб завтра в 9' → add_task, 'создай задачу подготовить отчет на завтра' → add_task",
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
            "description": "ЗАВЕРШИТЬ СУЩЕСТВУЮЩУЮ ЗАДАЧУ. Вызывай когда пользователь сообщает о выполнении задачи. Ключевые слова: 'сделал', 'выполнил', 'закончил', 'завершил', 'готово'. НЕ вызывай для создания, изменения или просмотра задач. Примеры: 'сделал почту' → complete_task, 'закончил отчет' → complete_task, 'выполнил задачу про презентацию' → complete_task",
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
            "description": "ПОКАЗАТЬ СПИСОК ЗАДАЧ. Вызывай ТОЛЬКО когда пользователь хочет посмотреть задачи. Ключевые слова: 'покажи задачи', 'список задач', 'мои задачи', 'что запланировано'. НЕ вызывай для создания, завершения или изменения задач. Примеры: 'покажи мои задачи' → list_tasks, 'что у меня запланировано' → list_tasks",
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
            "description": "ПЕРЕНЕСТИ ВРЕМЯ СУЩЕСТВУЮЩЕЙ ЗАДАЧИ. Вызывай ТОЛЬКО когда пользователь хочет изменить время задачи. Ключевые слова: 'перенеси', 'измени время', 'поставь на другое время'. НЕ вызывай для создания новых задач или завершения. Примеры: 'перенеси встречу на завтра в 16:00' → reschedule_task, 'измени время задачи про почту на 15:30' → reschedule_task",
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
            "description": "ИЗМЕНИТЬ НАЗВАНИЕ ИЛИ ОПИСАНИЕ СУЩЕСТВУЮЩЕЙ ЗАДАЧИ. Вызывай ТОЛЬКО когда пользователь хочет изменить текст задачи (название, описание). Ключевые слова: 'измени название', 'исправь задачу', 'обнови описание'. НЕ вызывай для изменения времени (используй reschedule_task). Примеры: 'измени название задачи на X' → edit_task, 'добавь описание к задаче Y' → edit_task",
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
            "description": "УДАЛИТЬ ЗАДАЧУ. Вызывай ТОЛЬКО когда пользователь хочет удалить задачу. Ключевые слова: 'удали', 'убери', 'сотри', 'больше не нужна'. НЕ вызывай для завершения задач (используй complete_task). Примеры: 'удали задачу про встречу' → delete_task, 'убери напоминание о покупках' → delete_task",
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
            "name": "set_recurring_task",
            "description": "ТОЛЬКО ДЛЯ ПОВТОРЯЮЩИХСЯ ЗАДАЧ! СТРОГО ЗАПРЕЩЕНО использовать для обычных задач. Вызывай ТОЛЬКО когда пользователь хочет регулярные напоминания. Ключевые слова: 'каждый день', 'еженедельно', 'каждую неделю', 'повторять', 'регулярно'. СТРОГО ЗАПРЕЩЕНО: для обычных задач, для переноса времени, для изменения задач. Примеры: 'напоминай о зарядке каждый день в 8:00' → set_recurring_task, 'проверяй почту каждую неделю по понедельникам' → set_recurring_task. ЗАПРЕЩЕНО: 'напомни купить хлеб завтра' (используй add_task), 'перенеси задачу на завтра' (используй reschedule_task)",
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
    {
        "type": "function",
        "function": {
            "name": "update_profile",
            "description": "ТОЛЬКО ДЛЯ ОБНОВЛЕНИЯ ПРОФИЛЯ ПОЛЬЗОВАТЕЛЯ! СТРОГО ЗАПРЕЩЕНО использовать для задач, памяти или поиска. Вызывай ТОЛЬКО когда пользователь хочет изменить личную информацию. Ключевые слова: 'обнови профиль', 'измени город', 'добавь навыки'. СТРОГО ЗАПРЕЩЕНО: для создания задач, для сохранения предпочтений, для поиска партнеров. Примеры: 'обнови мой профиль: город Москва' → update_profile, 'добавь в профиль навыки Python' → update_profile. ЗАПРЕЩЕНО: 'запомни что я люблю чай' (используй update_user_memory), 'найди партнеров' (используй find_partners)",
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
            "description": "ТОЛЬКО ДЛЯ ПОИСКА ПАРТНЕРОВ! СТРОГО ЗАПРЕЩЕНО использовать для других действий. Вызывай ТОЛЬКО когда пользователь ищет контакты или партнеров. Ключевые слова: 'найди партнеров', 'поищи контакты', 'кто может помочь'. СТРОГО ЗАПРЕЩЕНО: для создания задач, для обновления профиля, для разговоров. Примеры: 'найди партнеров по интересам' → find_partners, 'поищи контакты для проекта' → find_partners. ЗАПРЕЩЕНО: 'обнови профиль' (используй update_profile), 'создай задачу' (используй add_task)",
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
            "description": "ТОЛЬКО ДЛЯ ПОЛУЧЕНИЯ ДЕТАЛЕЙ ЗАДАЧИ! СТРОГО ЗАПРЕЩЕНО использовать для других действий. Вызывай ТОЛЬКО когда пользователь хочет подробности одной задачи. Ключевые слова: 'детали задачи', 'покажи задачу', 'что в задаче'. СТРОГО ЗАПРЕЩЕНО: для обновления профиля, для создания задач, для списка всех задач. Примеры: 'покажи детали задачи про презентацию' → get_task_details, 'что в задаче о встрече' → get_task_details. ЗАПРЕЩЕНО: 'обнови профиль' (используй update_profile), 'покажи все задачи' (используй list_tasks)",
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
            "description": "ТОЛЬКО ДЛЯ СОХРАНЕНИЯ ПАМЯТИ! СТРОГО ЗАПРЕЩЕНО использовать для других действий. Вызывай ТОЛЬКО когда пользователь хочет запомнить личные предпочтения или информацию. Ключевые слова: 'запомни', 'помни что', 'я предпочитаю'. СТРОГО ЗАПРЕЩЕНО: для делегирования задач, для создания задач, для разговоров. Примеры: 'запомни что я предпочитаю чай' → update_user_memory, 'помни что у меня аллергия' → update_user_memory. ЗАПРЕЩЕНО: 'делегируй задачу' (используй delegate_task), 'создай задачу' (используй add_task)",
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
            "description": "УДАЛИТЬ ВСЕ ЗАДАЧИ. Вызывай ТОЛЬКО когда пользователь хочет очистить все задачи. Ключевые слова: 'удали все', 'очисти список', 'удали все задачи'. ОПАСНАЯ ОПЕРАЦИЯ! Примеры: 'удали все мои задачи' → delete_all_tasks",
            "parameters": {
                "type": "object",
                "properties": {},
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