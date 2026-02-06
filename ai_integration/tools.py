# Enhanced TOOLS with clear, specific descriptions to prevent AI hallucinations

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
                        "description": "⚠️ ОБЯЗАТЕЛЬНО для задач! Время напоминания в ЛЮБОМ формате: 'завтра в 9:00', 'через 2 часа', 'послезавтра в 14:30', '15:00', 'YYYY-MM-DD HH:MM'. ЕСЛИ пользователь НЕ УКАЗАЛ - СПРОСИ 'на когда?' перед вызовом add_task(). НЕ СОЗДАВАЙ задачу без времени молча!",
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
            "description": "⚠️ ЗАВЕРШИТЬ СУЩЕСТВУЮЩУЮ ЗАДАЧУ. ОБЯЗАТЕЛЬНО вызывай когда пользователь сообщает о выполнении задачи! Ключевые слова: 'сделал', 'выполнил', 'закончил', 'завершил', 'готово', 'сделано', 'выполнено'. Примеры: 'я завершил встречу' → complete_task({'task_title': 'встречу'}), 'сделал почту' → complete_task({'task_title': 'почту'}), 'закончил отчет' → complete_task({'task_title': 'отчет'}), 'готово' → complete_task({})",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_title": {
                        "type": "string",
                        "description": "Ключевые слова из названия задачи из сообщения пользователя. Примеры: 'готово, купил молоко' → 'молоко', 'сделал отчет' → 'отчет', 'завершил встречу' → 'встречу'. Достаточно одного-двух ключевых слов. Можно оставить пустым если пользователь сказал просто 'готово' без уточнения.",
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
            "name": "analyze_goal_progress",
            "description": "🤖 АНАЛИЗИРУЕТ ПРОГРЕСС ПО ЦЕЛЯМ пользователя и автоматически отмечает достигнутые цели. Вызывай ПЕРИОДИЧЕСКИ или когда пользователь спрашивает о прогрессе по целям. НЕ вызывай при каждом сообщении - только при релевантных запросах о целях или прогрессе.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "integer",
                        "description": "Telegram ID пользователя для анализа целей"
                    }
                },
                "required": ["user_id"]
            }
        }
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
                        "description": "Ключевые слова для поиска задачи (НЕ точное название). ОПЦИОНАЛЬНО - если не указано, будет перенесена последняя активная задача. Примеры: 'почта' найдёт 'Проверить почту', 'встреч' найдёт 'Встреча с командой', 'молоко' найдёт 'Купить молоко'",
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
                    "task_id": {
                        "type": "integer",
                        "description": "ID задачи для принятия",
                    },
                },
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
                "properties": {
                    "task_id": {
                        "type": "integer",
                        "description": "ID задачи для отклонения",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Причина отказа",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_worker_task",
            "description": "Создать фоновую задачу (worker) для PREMIUM пользователей. Выполняется минимум раз в час. Неограниченное количество задач на пользователя. Поддерживает мониторинг металлов, валют, акций с техническим анализом и анализом объемов.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_description": {
                        "type": "string",
                        "description": "Описание задачи для worker, например 'Мониторинг золота' или 'Мониторинг погоды в Москве'"
                    },
                    "interval_minutes": {
                        "type": "integer",
                        "description": "Интервал выполнения в минутах, минимум 60 (1 час)",
                        "default": 1440
                    },
                    "action": {
                        "type": "string",
                        "description": "Тип действия: 'monitor_asset' для металлов/валют/акций, 'monitor_weather' для погоды"
                    },
                    "asset_type": {
                        "type": "string",
                        "description": "Тип актива для мониторинга: 'metal' для металлов, 'currency' для валют, 'stock' для акций (только для monitor_asset)",
                        "enum": ["metal", "currency", "stock"]
                    },
                    "symbol": {
                        "type": "string",
                        "description": "Символ актива: для металлов - 'GOLD' или 'SILVER', для валют - 'EURUSD', для акций - 'AAPL' (только для monitor_asset)"
                    },                    "analysis_type": {
                        "type": "string",
                        "description": "Тип анализа: 'price_monitoring' - простой мониторинг цены, 'technical_analysis' - технический анализ с индикаторами, 'volume_analysis' - анализ объема торгов",
                        "enum": ["price_monitoring", "technical_analysis", "volume_analysis"],
                        "default": "price_monitoring"
                    },
                    "response_style": {
                        "type": "string",
                        "description": "Стиль ответа: 'formal' - формальный отчет, 'conversational' - разговорный стиль как у человека",
                        "enum": ["formal", "conversational"],
                        "default": "formal"
                    },
                    "threshold": {
                        "type": "number",
                        "description": "Порог для уведомления: для активов - цена ниже порога, для погоды - температура ниже порога"
                    },
                    "city": {
                        "type": "string",
                        "description": "Город для мониторинга погоды (только для monitor_weather)",
                        "default": "Moscow"
                    },
                    "weather_condition": {
                        "type": "string",
                        "description": "Условие погоды для уведомления, например 'дождь', 'снег' (только для monitor_weather)"
                    }
                },
                "required": ["task_description", "action"]
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
            "name": "delete_worker_task",
            "description": "Удалить существующую фоновую задачу (worker). Используй когда пользователь хочет остановить или изменить свою фоновую задачу.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
]
