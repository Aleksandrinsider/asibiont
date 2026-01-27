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
            "name": "reschedule_task",
            "description": "Перенести существующую задачу на новое время. Используй когда пользователь хочет изменить время напоминания задачи.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_title": {
                        "type": "string",
                        "description": "Название задачи для переноса",
                    },
                    "new_time": {
                        "type": "string",
                        "description": "Новое время в формате YYYY-MM-DD HH:MM или HH:MM (если сегодня)",
                    },
                },
                "required": ["task_title", "new_time"],
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
            "name": "get_delegation_progress_for_task",
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