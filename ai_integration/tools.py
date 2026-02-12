# Enhanced TOOLS with clear, specific descriptions to prevent AI hallucinations

# Premium functions available only on higher tiers
PREMIUM_FUNCTIONS = {
    'set_contact_alert',
    'set_activity_alert',
    'research_and_plan',
    'set_content_strategy',
    'toggle_autonomous_feature'
}

STANDARD_FUNCTIONS = PREMIUM_FUNCTIONS - {
    'set_contact_alert',
    'set_activity_alert',
    'set_content_strategy',
    'toggle_autonomous_feature'
}

def get_available_tools(subscription_tier):
    """
    Filter tools based on user's subscription tier using dynamic tool discovery
    
    Args:
        subscription_tier: User's subscription tier (LIGHT, STANDARD, PREMIUM) - can be enum, string, or value
        
    Returns:
        List of available tools for the tier
    """
    from .dynamic_tools import tool_discovery
    
    # Handle different input types
    if hasattr(subscription_tier, 'value'):
        tier_value = subscription_tier.value
    elif isinstance(subscription_tier, str):
        # Handle both 'PREMIUM' and 'SubscriptionTier.PREMIUM'
        if subscription_tier.startswith('SubscriptionTier.'):
            tier_value = subscription_tier.split('.', 1)[1]
        else:
            tier_value = subscription_tier
    else:
        tier_value = str(subscription_tier).upper()
    
    # Use dynamic tool filtering
    return tool_discovery.get_available_tools_for_tier(tier_value)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "find_relevant_contacts_for_task",
            "description": "🎯 Найти контакты релевантные для конкретной задачи или активности. Используй ВСЕГДА когда пользователь создает задачу связанную с активностью (спорт, бизнес встреча, обучение) или когда уместно предложить партнера. Примеры: 'пойти на пробежку' → найти бегунов, 'обсудить стартап' → найти предпринимателей",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_description": {
                        "type": "string",
                        "description": "Описание задачи или активности для поиска релевантных контактов. Примеры: 'пойти на пробежку завтра', 'найти партнера для стартапа', 'изучить Python'"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Максимальное количество контактов для возврата (по умолчанию 5)",
                        "default": 5
                    }
                },
                "required": ["task_description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": "⚠️ ТОЛЬКО ДЛЯ НОВЫХ ЗАДАЧ! Создает новую задачу с напоминанием. После создания задачи про активность - ОБЯЗАТЕЛЬНО вызови find_relevant_contacts_for_task чтобы предложить подходящих людей. Не использовать для переноса существующих задач (используй reschedule_task)!",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Краткое название задачи (2-5 слов). Извлекай суть из запроса пользователя.",
                    },
                    "description": {
                        "type": "string",
                        "description": "⚠️ ДОПОЛНИТЕЛЬНЫЕ детали, НЕ повторяющие название! Оставляй ПУСТЫМ если пользователь дал только название без деталей. Примеры: 'проверить почту' → description='', 'встретиться с командой обсудить новый проект и бюджет' → description='обсудить новый проект и бюджет', 'купить молоко, хлеб и яйца' → description='молоко, хлеб, яйца'. НИКОГДА не дублируй название в описании!",
                    },
                    "reminder_time": {
                        "type": "string",
                        "description": "⚠️ КРИТИЧЕСКИ ВАЖНО! Время напоминания ОБЯЗАТЕЛЬНО для всех задач. ЕСЛИ в запросе пользователя ЕСТЬ указание времени (примеры: 'завтра в 9:00', 'через 2 часа', 'послезавтра в 14:30', '15:00') - СРАЗУ используй его в add_task(). ЕСЛИ времени НЕТ ('создай задачу проверить почту' без времени) - сначала СПРОСИ 'На какое время назначить?', а затем вызови add_task(). Поддерживаемые форматы: 'завтра в 9:00', 'через 2 часа', 'послезавтра в 14:30', '15:00', 'YYYY-MM-DD HH:MM'.",
                    },
                    "is_recurring": {
                        "type": "boolean",
                        "description": "Установи true если задача повторяющаяся (ежедневно, еженедельно и т.д.). Ключевые слова: 'каждый день', 'ежедневно', 'каждую неделю', 'еженедельно', 'повторять', 'регулярно'.",
                    },
                    "recurrence_pattern": {
                        "type": "string",
                        "description": "Паттерн повторения: 'daily', 'weekly', 'monthly', 'yearly'. Только если is_recurring=true.",
                        "enum": ["daily", "weekly", "monthly", "yearly"]
                    },
                    "recurrence_interval": {
                        "type": "integer",
                        "description": "Интервал повторения (каждые N дней/недель/месяцев). По умолчанию 1. Только если is_recurring=true.",
                        "default": 1
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
            "description": "⚠️ ОТМЕТИТЬ ВЫПОЛНЕНИЕ ЗАДАЧИ (ЗАКРЫТЬ ЗАДАЧУ). Ключевые фразы: 'сделал задачу', 'выполнил задачу', 'закончил задачу', 'завершил задачу', 'закрыть задачу', 'отметить выполнение'. Примеры: 'сделал задачу тест' → complete_task({'task_title': 'тест'}), 'выполнил презентацию' → complete_task({'task_title': 'презентацию'}). ВАЖНО: ВОЗМОЖНЫ КОРОТКИЕ ФРАЗЫ БЕЗ слова 'задачу': 'готово', 'сделал', 'проверил' → complete_task({}).",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_title": {
                        "type": "string",
                        "description": "⚠️ ОПЦИОНАЛЬНО! Ключевые слова из названия задачи. МОЖНО ОСТАВИТЬ ПУСТЫМ если есть ТЕКУЩАЯ ЗАДАЧА В ФОКУСЕ в системном промпте! Примеры: 'купил молоко' → 'молоко', 'сделал отчет' → 'отчет'. При коротких фразах ('готово', 'сделал') - НЕ УКАЗЫВАЙ, оставь пустым!",
                    },
                    "completion_note": {
                        "type": "string",
                        "description": "Заметка о результат выполнения (опционально)",
                    },
                },
                "required": [],  # task_title опционален
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reschedule_task",
            "description": "⚠️ ПЕРЕНЕСТИ ВРЕМЯ СУЩЕСТВУЮЩЕЙ ЗАДАЧИ. ОБЯЗАТЕЛЬНО используй когда пользователь хочет ИЗМЕНИТЬ ВРЕМЯ задачи. Ключевые слова: 'перенеси', 'давай перенесем', 'измени время', 'поставь на другое время', 'отложи', 'подвинь'. ВСЕГДА используй reschedule_task для переноса, НИКОГДА не создавай новую задачу через add_task. Если название не указано, будет перенесена последняя активная задача. Примеры: 'перенеси встречу на завтра в 16:00' → reschedule_task(task_title='встреча', new_time='завтра в 16:00'), 'перенеси проверку почты на 5 минут' → reschedule_task(task_title='почт', new_time='через 5 минут'), 'отложи на час' → reschedule_task(new_time='через 1 час'), 'перенеси её через 15 минут' → reschedule_task(new_time='через 15 минут')",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_title": {
                        "type": "string",
                        "description": "Ключевые слова для поиска задачи. ОПЦИОНАЛЬНО - если не указано, будет перенесена последняя активная задача. ВАЖНО: извлекай УНИКАЛЬНЫЕ ключевые слова из контекста - если есть @username, имена, специфические термины - используй их! Примеры: 'почта' найдёт 'Проверить почту', '@battle сообщение' найдёт 'Написать @battle_test_user', 'notion api' найдёт 'Настроить Notion API'",
                    },
                    "new_time": {
                        "type": "string",
                        "description": "Новое время в ЛЮБОМ формате: 'YYYY-MM-DD HH:MM', 'HH:MM', 'через 2 часа', 'завтра в 10:00', 'послезавтра в 14:00', 'через 15 минут'",
                    },
                },
                "required": ["new_time"],
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
            "description": "⚠️ УДАЛИТЬ ЗАДАЧУ [ОБЯЗАТЕЛЬНО ВЫЗОВИ ФУНКЦИЮ!]. Вызывай СРАЗУ когда пользователь хочет удалить задачу. Ключевые слова: 'удали', 'убери', 'сотри', 'больше не нужна'. НЕ ПРОСТО ГОВОРИ 'удалил' - ОБЯЗАТЕЛЬНО ВЫЗОВИ delete_task()! НЕ вызывай для завершения задач (используй complete_task). Примеры: 'удали задачу про встречу' → delete_task('встречу'), 'убери напоминание о покупках' → delete_task('покупках')",
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
            "name": "update_profile",
            "description": "АВТОМАТИЧЕСКОЕ ИЗВЛЕЧЕНИЕ И ОБНОВЛЕНИЕ ПРОФИЛЯ! ДОБАВЛЯЕТ данные в списочные поля (interests, skills), НЕ заменяет их. Для целей (goals) использует УМНОЕ ОБЪЕДИНЕНИЕ - проверяет семантическое сходство и объединяет похожие цели вместо дублирования. Вызывай ТОЛЬКО при ЯВНОМ упоминании личных данных пользователем в сообщении. СТРОГО ЗАПРЕЩЕНО выдумывать или генерировать данные. ПРИМЕРЫ: 'я из Москвы' → update_profile(city='Москва'), 'работаю в ASI Biont' → update_profile(company='ASI Biont'), 'умею программировать' → update_profile(skills='программирование') [ДОБАВИТ к существующим], 'люблю покер' → update_profile(interests='покер') [ДОБАВИТ к существующим], 'хочу развивать бизнес' → update_profile(goals='развивать бизнес') [УМНО ОБЪЕДИНИТ с похожими целями]. ЗАПРЕЩЕНО: вызывать без явного упоминания данных, выдумывать данные.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "Город проживания или работы. Примеры: Москва, Санкт-Петербург, Екатеринбург"},
                    "birth_date": {"type": "string", "description": "День рождения в формате DD.MM.YYYY. Примеры: 04.06.1985, 15.03.1990"},
                    "company": {"type": "string", "description": "Название компании работодателя. Примеры: ASI Biont, Яндекс, Google"},
                    "position": {"type": "string", "description": "Должность, роль в компании. Примеры: Директор, Разработчик, Менеджер"},
                    "skills": {"type": "string", "description": "Профессиональные навыки через запятую. Примеры: Управление, разработка, Python, дизайн"},
                    "interests": {"type": "string", "description": "Личные интересы и хобби через запятую. Примеры: ИИ, технологии, бизнес, книги, спорт"},
                    "goals": {"type": "string", "description": "Цели, планы, желания. Примеры: развивать бизнес, изучить Python, найти партнеров. Функция УМНО объединит похожие цели."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "smart_update_profile",
            "description": "УМНОЕ РЕДАКТИРОВАНИЕ ПРОФИЛЯ С ВЫБОРОМ ДЕЙСТВИЯ. Используй когда нужно ТОЧНО КОНТРОЛИРОВАТЬ как обновить профиль: добавить, заменить или умно объединить. Для целей поддерживает семантическое объединение похожих целей. ПРИМЕРЫ: 'замени все цели на новую' → smart_update_profile(field='goals', value='новая цель', action='replace'), 'добавь навык Python' → smart_update_profile(field='skills', value='Python', action='add'), 'обнови город' → smart_update_profile(field='city', value='Москва', action='replace').",
            "parameters": {
                "type": "object",
                "properties": {
                    "field": {
                        "type": "string",
                        "description": "Поле для обновления",
                        "enum": ["goals", "interests", "skills", "city", "company", "position"]
                    },
                    "value": {
                        "type": "string",
                        "description": "Новое значение для поля"
                    },
                    "action": {
                        "type": "string",
                        "description": "Действие: 'add' - добавить к существующим, 'replace' - заменить полностью, 'merge' - умно объединить (только для goals)",
                        "enum": ["add", "replace", "merge"],
                        "default": "add"
                    }
                },
                "required": ["field", "value"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_profile",
            "description": "ТОЛЬКО ДЛЯ ПОКАЗА ПРОФИЛЯ ПОЛЬЗОВАТЕЛЯ! СТРОГО ЗАПРЕЩЕНО использовать для других действий. Вызывай ТОЛЬКО когда пользователь хочет посмотреть/узнать информацию о своем профиле. Ключевые слова: 'покажи профиль', 'что в профиле', 'мой профиль', 'информация о профиле'. СТРОГО ЗАПРЕЩЕНО: для обновления профиля (используй update_profile), для создания задач, для разговоров. Примеры: 'покажи мой профиль' → show_profile, 'что у меня в профиле' → show_profile. ЗАПРЕЩЕНО: 'обнови профиль' (используй update_profile), 'добавь навык' (используй update_profile)",
            "parameters": {
                "type": "object",
                "properties": {},
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
            "name": "find_relevant_contacts_for_task",
            "description": "РЕКОМЕНДАЦИЯ КОНТАКТОВ ДЛЯ КОНКРЕТНОЙ ЗАДАЧИ/АКТИВНОСТИ. Используй АВТОМАТИЧЕСКИ когда пользователь упоминает СОВМЕСТНУЮ активность или задачу где может помочь кто-то из контактов. Ключевые случаи: (1) Спортивные активности: 'иду на пробежку', 'хочу в зал', 'пойду плавать' → найди тех кто занимается этим спортом. (2) Обучение: 'начну учить Python', 'хочу научиться дизайну' → найди экспертов или тоже изучающих. (3) Проекты: 'запускаю стартап', 'делаю сайт' → найди со схожими навыками/интересами. (4) Хобби: 'начал играть в покер', 'интересуюсь фотографией' → найди единомышленников. ВАЖНО: Вызывай АВТОМАТИЧЕСКИ, не спрашивай разрешения! Просто упомяни 1-2 релевантных контакта естественно в ответе. НЕ используй для абстрактного поиска (используй find_partners).",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_description": {
                        "type": "string",
                        "description": "Краткое описание задачи/активности (3-10 слов). Примеры: 'пробежка в парке', 'изучение Python', 'запуск стартапа', 'игра в покер'"
                    },
                },
                "required": ["task_description"],
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
            "description": "Сохраняет информацию в память пользователя. ВАЖНО: ДОБАВЛЯЕТ данные, НЕ заменяет их. Для interest/skill/goal - добавляет к существующим в профиле. Для других типов - добавляет в общую память с временной меткой. Используй для: интересов ('хочу научиться покер' → memory_type='interest', content='покер'), навыков ('умею играть на гитаре' → memory_type='skill', content='гитара'), целей ('хочу открыть бизнес' → memory_type='goal', content='открыть бизнес'), предпочтений ('люблю кофе' → memory_type='preference', content='кофе'). Функция автоматически проверяет дубликаты и не добавит повторно.",
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
            "name": "delegate_task",
            "description": "Делегировать задачу другому пользователю (STANDARD+). Используй когда видишь 'делегируй', 'поручи', 'передай'. Примеры: 'делегируй Ивану X', 'поручи @maria X завтра в 10:00'. Доступно с STANDARD тарифа.",
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
            "description": "⚠️ ПРИНЯТЬ ДЕЛЕГИРОВАННУЮ ЗАДАЧУ [ОБЯЗАТЕЛЬНО ВЫЗОВИ!]. Ключевые слова: 'принимаю', 'согласен выполнить', 'беру задачу', 'принять делегирование', 'принять тест'. Можешь использовать task_title напрямую (проще) или task_id. НЕ ПРОСТО ГОВОРИ 'принял' - ОБЯЗАТЕЛЬНО ВЫЗОВИ accept_delegated_task()!",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "integer",
                        "description": "ID задачи для принятия (опционально если есть task_title)",
                    },
                    "task_title": {
                        "type": "string",
                        "description": "Название или часть названия задачи (опционально если есть task_id). Примеры: 'тест', 'встреча', 'отчет'",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reject_delegated_task",
            "description": "⚠️ ОТКЛОНИТЬ ДЕЛЕГИРОВАННУЮ ЗАДАЧУ [ОБЯЗАТЕЛЬНО ВЫЗОВИ!]. Ключевые слова: 'отклоняю', 'не буду делать', 'отказываюсь', 'отклонить делегирование', 'отклонить тест'. Можешь использовать task_title напрямую (проще) или task_id. НЕ ПРОСТО ГОВОРИ 'отклонил' - ОБЯЗАТЕЛЬНО ВЫЗОВИ reject_delegated_task()!",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "integer",
                        "description": "ID задачи для отклонения (опционально если есть task_title)",
                    },
                    "task_title": {
                        "type": "string",
                        "description": "Название или часть названия задачи (опционально если есть task_id). Примеры: 'тест', 'встреча', 'отчет'",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Причина отказа",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_activity_alert",
            "description": "🌟 PREMIUM: Настроить автоматическое уведомление об активностях других пользователей. Система будет мониторить задачи других и АВТОМАТИЧЕСКИ добавит информацию в твой следующий диалог через AI (проактивный контекст). Примеры: 'скажи когда кто-то пойдет на пробежку' → AI упомянет в следующем сообщении 'Кстати, @user123 планирует пробежку завтра в 7:00, хочешь присоединиться?'. Работает для: спорт (пробежка, зал, йога), бизнес (митапы, встречи), обучение (курсы, воркшопы), хобби (покер, кино). Информация приходит ЕСТЕСТВЕННО в разговоре, НЕ как push-уведомление.",
            "parameters": {
                "type": "object",
                "properties": {
                    "activity_type": {
                        "type": "string",
                        "description": "Тип активности для мониторинга. Примеры: 'пробежка', 'тренажерный зал', 'митап по AI', 'воркшоп', 'покер', 'кино', 'бизнес-встреча'"
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Ключевые слова для поиска в задачах других. Примеры: ['пробежка', 'бег', 'running'], ['митап', 'meetup', 'конференция'], ['покер', 'poker']"
                    },
                    "location": {
                        "type": "string",
                        "description": "Опционально: фильтр по городу. Примеры: 'Москва', 'Санкт-Петербург', 'любой'"
                    },
                    "frequency": {
                        "type": "string",
                        "enum": ["any", "regular", "one_time"],
                        "description": "Частота активности: 'any' - любые, 'regular' - только регулярные (еженедельные), 'one_time' - только разовые. По умолчанию 'any'",
                        "default": "any"
                    },
                    "enabled": {
                        "type": "boolean",
                        "description": "Включить (true) или отключить (false) уведомление. По умолчанию true",
                        "default": True
                    }
                },
                "required": ["activity_type", "keywords"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "Показать все активные задачи пользователя. Вызывай ТОЛЬКО когда спрашивают о задачах ('мои задачи', 'список', 'что запланировано'). НЕ вызывай для создания, завершения или других операций с задачами.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_time_conflicts",
            "description": "Проверить конфликты по времени перед созданием задачи. ОБЯЗАТЕЛЬНО вызывай ПЕРЕД add_task() чтобы убедиться, что время свободно. Возвращает информацию о конфликтах и предлагает альтернативное время если нужно.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reminder_time": {
                        "type": "string",
                        "description": "Время для проверки в формате 'завтра в 10:00', 'через 2 часа', '2026-01-15 14:30'"
                    }
                },
                "required": ["reminder_time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_contact_alert",
            "description": "⚠️ МОНИТОРИНГ КОНТАКТОВ ПО НАВЫКАМ/ИНТЕРЕСАМ [ДОСТУПНО - ВЫЗЫВАЙ!]: Настроить автоматическое уведомление о новых пользователях с нужными навыками/интересами. Ключевые слова: 'мониторь @user', 'отслеживай контакт', 'следи за пользователем', 'скажи когда появится Python разработчик'. ЕСЛИ пользователь говорит 'мониторь @user' или подобное - ОБЯЗАТЕЛЬНО ВЫЗОВИ set_contact_alert()! Система будет мониторить регистрации и обновления профилей, и АВТОМАТИЧЕСКИ добавит информацию в твой следующий диалог через AI. Примеры: 'скажи когда появится специалист по продажам' → set_contact_alert(skill='продажи'), 'мониторь @test_user' → set_contact_alert(skill='test_user'). [ПРЕМИУМ функция, но ДОСТУПНА в твоем списке - значит ВЫЗЫВАЙ].",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {
                        "type": "string",
                        "description": "Навык для поиска. Примеры: 'продажи', 'Python', 'дизайн', 'маркетинг', 'бухгалтерия', 'управление проектами'"
                    },
                    "interest": {
                        "type": "string",
                        "description": "Интерес для поиска. Примеры: 'стартапы', 'ИИ', 'блокчейн', 'покер', 'недвижимость', 'инвестиции'"
                    },
                    "city": {
                        "type": "string",
                        "description": "Опционально: фильтр по городу. Примеры: 'Москва', 'Санкт-Петербург', 'любой'"
                    },
                    "position": {
                        "type": "string",
                        "description": "Опционально: должность/роль. Примеры: 'CEO', 'основатель', 'разработчик', 'менеджер'"
                    },
                    "enabled": {
                        "type": "boolean",
                        "description": "Включить (true) или отключить (false) уведомление. По умолчанию true",
                        "default": True
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_marketing_content",
            "description": "🚀 AI МАРКЕТИНГ (STANDARD+): Автоматическая генерация профессионального маркетингового контента для привлечения клиентов. AI создаст цепляющий заголовок, текст поста, хэштеги и призыв к действию. ⚠️ НЕ СПРАШИВАЙ ДЕТАЛИ - ГЕНЕРИРУЙ СРАЗУ с тем что есть! Используй разумные defaults: если аудитория не указана → 'предприниматели 25-40', если платформа не указана → 'telegram'. Используй когда пользователь хочет: написать пост для соцсетей, создать рекламу, привлечь клиентов, продвинуть продукт. Примеры: 'напиши пост про AI' → generate_marketing_content(product_name='AI инструменты', target_audience='предприниматели 25-40', platform='telegram'), 'создай рекламу для Instagram' → generate_marketing_content(..., platform='instagram'). Доступно с STANDARD или PREMIUM.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_name": {
                        "type": "string",
                        "description": "Название продукта/услуги/проекта. Примеры: 'AI-бот для задач', 'Курсы по Python', 'Консалтинговые услуги'"
                    },
                    "target_audience": {
                        "type": "string",
                        "description": "Целевая аудитория продукта. Примеры: 'предприниматели 25-40 лет', 'студенты IT', 'владельцы малого бизнеса', 'стартаперы'"
                    },
                    "platform": {
                        "type": "string",
                        "description": "Платформа для публикации. Варианты: 'telegram', 'vk', 'instagram', 'twitter', 'linkedin'",
                        "enum": ["telegram", "vk", "instagram", "twitter", "linkedin"]
                    },
                    "goal": {
                        "type": "string",
                        "description": "Цель контента: 'привлечение' (новых клиентов), 'продажа' (закрыть сделку), 'удержание' (engagement), 'бренд' (узнаваемость)",
                        "enum": ["привлечение", "продажа", "удержание", "бренд"],
                        "default": "привлечение"
                    }
                },
                "required": ["product_name", "target_audience", "platform"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "research_topic",
            "description": "🔍 УНИВЕРСАЛЬНЫЙ ПОИСК И АНАЛИЗ (ВСЕ ТАРИФЫ): AI-powered исследование через Google Search с адаптивным анализом под ваш тариф. LIGHT: быстрый обзор (3-5 источников), STANDARD: детальный анализ (8-10 источников), PREMIUM: глубокий анализ с планом действий (12-15 источников). Автоматически подстраивается под сложность темы и дает actionable insights. Примеры: 'рынок AI-агентов', 'тренды в стартапах 2026', 'конкуренты в нише X'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Тема для исследования. Примеры: 'рынок AI-ботов в России 2026', 'конкуренты в нише образовательных платформ', 'тренды B2B маркетинга'"
                    },
                    "depth": {
                        "type": "string",
                        "description": "Глубина анализа: 'basic' (базовый), 'full' (полный), 'deep' (глубокий). По умолчанию 'full'",
                        "default": "full",
                        "enum": ["basic", "full", "deep"]
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "research_and_plan",
            "description": "🎯 КОМПЛЕКСНЫЙ АНАЛИЗ РЫНКА + ПЛАН ДЕЙСТВИЙ (STANDARD+): Глубокое исследование рынка через SERPER с анализом конкурентов, трендов и возможностей. Создает персонализированный план действий с конкретными задачами. Идеально для: запуска бизнеса, изучения ниши, стратегического планирования. Примеры: 'проанализируй рынок AI-агентов и составь план продвижения', 'изучи конкурентов в нише X и предложи стратегию'. Доступно с STANDARD или PREMIUM.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Тема для комплексного анализа. Примеры: 'AI-агенты для бизнеса', 'стартап в сфере образования', 'B2B маркетинг 2026'"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "publish_to_telegram",
            "description": "📢 ПУБЛИКАЦИЯ В TELEGRAM (STANDARD+): Публикует пост в Telegram канал пользователя. Требуется настроенный telegram_channel в профиле. Используй ПОСЛЕ generate_marketing_content или когда пользователь просит 'опубликуй пост', 'запости это'. Бот должен быть админом канала. Доступно с STANDARD или PREMIUM.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Текст для публикации. Может быть простая строка или структурированный контент из generate_marketing_content. Поддерживает Markdown."
                    }
                },
                "required": ["content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_content_strategy",
            "description": "🎯 НАСТРОИТЬ СТРАТЕГИЮ КОНТЕНТА (STANDARD+): Сохраняет стратегию контента для автоматического маркетинга. Используй когда пользователь хочет настроить автопостинг или описывает что хочет видеть в постах. Примеры: 'хочу постить про свой бизнес', 'расскажи как настроить автопубликацию', 'буду публиковать кейсы по дизайну'. ВАЖНО: Спроси у пользователя детали если их нет - что постить, для кого, какая цель.",
            "parameters": {
                "type": "object",
                "properties": {
                    "strategy": {
                        "type": "string",
                        "description": "Описание стратегии контента от пользователя. Должно включать: о чем постить, какая цель, для какой аудитории. Сохраняй своими словами пользователя максимально подробно. Примеры: 'Пишу про веб-разработку для начинающих программистов, цель - привлечь учеников на курсы', 'Делюсь кейсами по дизайну интерьеров для владельцев квартир и домов, продаю услуги дизайнера'"
                    }
                },
                "required": ["strategy"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "toggle_autonomous_feature",
            "description": "⚙️ УПРАВЛЕНИЕ АВТОНОМНЫМИ ФУНКЦИЯМИ (PREMIUM): Включает/выключает автоматические Premium функции. Используй когда пользователь говорит: 'отключи автопостинг', 'больше не пости автоматически', 'выключи автоделегирование', 'включи обратно автоматику'. Доступно только для PREMIUM.",
            "parameters": {
                "type": "object",
                "properties": {
                    "feature": {
                        "type": "string",
                        "description": "Какую функцию управлять: 'marketing' (автопостинг), 'delegation' (автоделегирование), 'all' (все автономные функции)",
                        "enum": ["marketing", "delegation", "all"]
                    },
                    "enabled": {
                        "type": "boolean",
                        "description": "true = включить, false = выключить. Определяй из запроса пользователя: 'отключи' → false, 'включи' → true"
                    }
                },
                "required": ["feature", "enabled"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_news_trends",
            "description": "📰 НОВОСТИ И ТРЕНДЫ по интересам: AI ищет актуальные новости по теме пользователя и анализирует тренды. Идеально для: отслеживания изменений в индустрии, мониторинга конкурентов, поиска бизнес-возможностей. Примеры: 'новости в сфере AI', 'тренды в стартапах', 'что происходит в моей индустрии'. Доступно на всех тарифах.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Тема для поиска новостей. Примеры: 'искусственный интеллект', 'стартапы России', 'финтех', 'разработка ПО'"
                    },
                    "period": {
                        "type": "string", 
                        "description": "Период новостей: 'today' (сегодня), 'week' (неделя), 'month' (месяц)",
                        "enum": ["today", "week", "month"],
                        "default": "week"
                    },
                    "focus": {
                        "type": "string",
                        "description": "Фокус анализа: 'news' (просто новости), 'trends' (тренды и анализ), 'opportunities' (бизнес-возможности)",
                        "enum": ["news", "trends", "opportunities"],
                        "default": "trends"
                    }
                },
                "required": ["topic"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_topic_relevance",
            "description": "📊 ПРОВЕРКА АКТУАЛЬНОСТИ ТЕМЫ (LIGHT+): Быстрая проверка есть ли свежая информация по теме. Показывает актуальность темы без детального анализа. Примеры: 'актуальна ли тема блокчейн', 'есть ли новости про метавселенные', 'стоит ли изучать эту технологию'. Доступно всем пользователям.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Тема для проверки актуальности. Примеры: 'NFT', 'web3', 'квантовые компьютеры', 'солнечная энергетика'"
                    }
                },
                "required": ["topic"]
            }
        }
    },
]
