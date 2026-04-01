# Enhanced TOOLS with clear, specific descriptions to prevent AI hallucinations
# Все инструменты доступны всем пользователям — оплата через токены за каждое действие

# Функции делегирования
DELEGATION_FUNCTIONS = {
    'delegate_task',
    'get_delegation_progress',
    'accept_delegated_task',
    'reject_delegated_task'
}

# Фоновые алерты (автоматические уведомления о контактах)
ALERT_FUNCTIONS = {
    'set_contact_alert',
}

# Инструменты исключённые из обнаружения (дубли, устаревшие)
# Функции-обработчики остаются в handlers.py, но не предлагаются AI
EXCLUDED_TOOLS = {
    'get_news_info',                     # дубль get_news_trends
    'check_topic_relevance',             # дубль research_topic
    'research_and_plan',                 # дубль research_topic(depth='deep')
    'smart_update_profile',              # дубль update_profile
    'update_user_memory',                # дубль update_profile
    'find_partners',                     # дубль find_relevant_contacts_for_task
    'get_task_details',                  # покрывается list_tasks + контекст
    'reschedule_task',                   # покрывается edit_task(reminder_time)
    'set_activity_alert',                # объединяется в set_contact_alert
    'analyze_situation_and_suggest_tasks', # AI собирает сам из контекста
    # Внутренние функции (не должны быть прямыми инструментами)
    'delegate_task_with_session',        # внутренняя обёртка delegate_task
    'get_tier_priority',                 # служебная
    'check_time_conflicts_sync',         # служебная
    'find_nearest_free_slot',            # служебная
    'generate_delegation_notification_async', # внутренняя
    'generate_delegation_notification',  # внутренняя
    'generate_progress_request',         # внутренняя
    'generate_delegation_response_notification_async', # внутренняя
    'schedule_delegation_monitoring',    # внутренняя
    'check_delegation_deadlines',        # внутренняя
    'check_subscription_status',         # служебная
    'create_subscription_payment',       # служебная (оплата через /buy)
    'cancel_subscription',               # служебная
    'suggest_trends_and_opportunities',  # дубль AI-анализа через research_topic
    'generate_marketing_content',        # прямой вызов через research_topic + create_post
    'show_profile',                      # профиль в контексте, не нужно вызывать как инструмент
    'analyze_group_opportunities',       # внутренняя
    'set_auto_post_time',                # покрывается set_content_strategy
    # 'publish_to_telegram' — разблокирован, AI может публиковать в TG-канал напрямую
    # 'get_weather_info' — разблокирован, AI должен вызывать для запросов о погоде
    # Внутренние утилиты (не инструменты для AI)
    'encrypt_data',                      # служебная криптография
    'decrypt_data',                      # служебная криптография
    'parse_time_to_datetime',            # внутренний парсер времени
    'find_task_flexible',                # внутренний поиск задач
    'resolve_task_reference',            # внутренний резолвер задач
    'get_user_context',                  # служебная (контекст уже в промпте)
    'generate_unified_recommendations',  # внутренняя аналитика
    'quick_topic_search',                # дубль research_topic (лёгкий)
    'get_partners_list',                 # дубль find_relevant_contacts_for_task
    # 'toggle_autonomous_feature' — разблокирован, AI может управлять автопостингом
    'cancel_delegation',                 # покрывается reject_delegated_task
    # web_search — РАЗБЛОКИРОВАН: используется автопилотом для прямого поиска
    'get_stock_info',                    # удалён — заменён research_topic
    'and_',                              # SQLAlchemy operator leak
    'or_',                               # SQLAlchemy operator leak
    # Дубли активных инструментов — убираем чтобы не путать AI
    'set_reminder',                      # дубль add_task(reminder_time)
    'update_goal',                       # дубль update_goal_progress(notes/status)
    'complete_goal',                     # дубль update_goal_progress(status='completed')
    'get_message_status',                # редко используется, покрывается get_incoming_messages
    'check_time_conflicts',              # внутренняя, вызывается автоматически в add_task
    'restore_task',                      # редкий кейс, покрывается add_task
    'get_stock_price',                   # дубль research_topic для финансовых данных
    'quick_topic_search',                # дубль research_topic(depth='basic')
    'research_and_plan',                 # дубль research_topic(depth='deep')
    'analyze_situation_and_suggest_tasks', # AI анализирует сам
    'analyze_group_opportunities',       # дубль find_relevant_contacts_for_task
    'find_partners',                     # дубль find_relevant_contacts_for_task
    'generate_marketing_content',        # AI генерирует сам через create_post
}

def get_available_tools(subscription_tier=None):
    """
    Возвращает все доступные инструменты. Тарифные ограничения убраны — оплата токенами.
    
    Args:
        subscription_tier: Deprecated, игнорируется. Оставлен для совместимости вызовов.
        
    Returns:
        List of available tools
    """
    from .dynamic_tools import tool_discovery
    
    # Check if dynamic tools are discovered
    if tool_discovery.discovered_tools:
        # Use dynamic tool filtering (returns all tools)
        return tool_discovery.get_available_tools_for_tier('ALL')
    else:
        # Fallback to static TOOLS — return all except excluded
        return [tool for tool in TOOLS 
               if tool['function']['name'] not in EXCLUDED_TOOLS]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "find_relevant_contacts_for_task",
            "description": "🎯 Найти контакты СРЕДИ ПОЛЬЗОВАТЕЛЕЙ ПЛАТФОРМЫ для задачи/активности (спорт, бизнес, обучение). Ищет только внутри ASI Biont. Для внешнего поиска используй web_search.",
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
            "name": "save_user_rule",
            "description": "Сохранить поведенческое правило или предпочтение пользователя в долгосрочную память. ВЫЗЫВАЙ ОБЯЗАТЕЛЬНО при любой фразе которая задаёт поведение на будущее или ОГРАНИЧЕНИЕ: 'не трогай X', 'не ищи Y', 'не привлекай тех кто...', 'игнорируй Z', 'исключи A', 'всегда делай B', 'при задаче C — используй D', 'агенты должны...', 'не нужно E', 'только F', 'избегай G', 'пропускай H'. ОСОБО ВАЖНО для запретов и исключений! Примеры ОБЯЗАТЕЛЬНОГО вызова: 'не ищи Telegram каналы' → правило 'не использовать find_and_message для поиска Telegram каналов'; 'контакты уже в ASI Biont не нужны' → правило об исключениях; 'не пиши на русском' → языковое правило; 'сначала всегда проверяй X' → порядок действий; 'игнорируй компании из США' → фильтр поиска. Правило автоматически передаётся всем агентам пользователя в каждом цикле. НЕ вызывай для одноразовых просьб — только для инструкций на будущее.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rule": {
                        "type": "string",
                        "description": "Чёткая формулировка правила — что нужно делать или НЕ делать, в каком контексте. Максимально конкретно и без двусмысленности. Для запретов формулируй ЯВНО: 'не использовать инструмент X для Y', 'исключить Z из поиска', 'всегда избегать A'."
                    }
                },
                "required": ["rule"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": "Создать задачу с напоминанием. 'Напомни X через 15 минут / в 14:30 / завтра в 9' → вызывай СРАЗУ с reminder_time. 'Напомни X' БЕЗ времени → НЕ вызывай этот инструмент, задай пользователю 1 вопрос: 'На какое время поставить напоминание?' Для изменения существующей задачи — edit_task, не add_task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Краткое название задачи (2-5 слов). Извлекай суть из запроса пользователя.",
                    },
                    "description": {
                        "type": "string",
                        "description": "НЕ ЗАПОЛНЯЙ этот параметр — название задачи уже содержит всю нужную информацию. Оставь пустым или не передавай вообще.",
                    },
                    "reminder_time": {
                        "type": "string",
                        "description": "Время напоминания — ОБЯЗАТЕЛЬНО если создаёшь задачу. Форматы: 'через 15 минут', 'через 2 часа', 'завтра в 9:00', 'в 14:30', 'YYYY-MM-DD HH:MM'. Если время НЕ указано — НЕ вызывай add_task, сначала спроси пользователя.",
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
                    "goal_title": {
                        "type": "string",
                        "description": "Название цели, к которой относится задача. Если задача явно связана с существующей целью пользователя — укажи название цели. Примеры: задача 'привлечь 5 тестовых пользователей' → goal_title='Раскрутить нового ИИ агента'.",
                    },
                },
                "required": ["title", "reminder_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_note",
            "description": "Сохранить заметку — запись БЕЗ напоминания и дедлайна. ВСЕГДА используй когда пользователь говорит: 'запиши', 'в заметки', 'запомни', 'сохрани в заметки', 'добавь в заметки', 'заметка', 'запиши себе'. НЕ путай с add_task — заметки не имеют времени напоминания.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Текст заметки, который нужно сохранить.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Краткий заголовок заметки (опционально, до 60 символов). Если не указан — берётся из начала content.",
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Отметить задачу выполненной. Ключевые слова: сделал, готово, завершил, разобрался, заказал, купил, оплатил, отправил, написал, позвонил, записался, настроил, починил, забрал, получил, прошёл, установил, собрал, приготовил, убрал — ЛЮБОЙ глагол совершённого вида означающий что действие выполнено. Заполняй completion_note.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "integer",
                        "description": "ID задачи (опционально, если известен). Приоритетнее task_title.",
                    },
                    "task_title": {
                        "type": "string",
                        "description": "Ключевые слова задачи (опционально). При 'готово'/'сделал' — оставь пустым.",
                    },
                    "completion_note": {
                        "type": "string",
                        "description": "Заметка о результате выполнения (опционально)",
                    },
                },
                "required": [],  # task_title опционален
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_task",
            "description": "Изменить существующую задачу (название, описание, время). Ключевые слова: измени, исправь, перенеси, обнови, через N минут. Если задача уже создана — edit_task, не add_task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "integer",
                        "description": "ID задачи (опционально, если известен). Приоритетнее task_title.",
                    },
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
            "description": "Удалить задачу НАВСЕГДА. Ключевые слова: 'удали', 'убери', 'сотри', 'удалить'. ВАЖНО: если пользователь говорит 'удали задачу' — ВСЕГДА вызывай delete_task, даже если он упоминает что задача уже сделана. НЕ путай с complete_task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "integer",
                        "description": "ID задачи (опционально, если известен). Приоритетнее task_title.",
                    },
                    "task_title": {
                        "type": "string",
                        "description": "Название или часть названия задачи для удаления. Примеры: 'купить', 'встреча', 'хлеб'",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Причина удаления (опционально)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_profile",
            "description": "Сохранить НОВЫЕ данные о пользователе в профиль. Вызывай только когда появляется НОВАЯ информация, которой ещё нет в профиле. Не обновляй то, что уже записано. ДОБАВЛЯЕТ к существующим данным (не заменяет).",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "Город проживания. Примеры: Москва, Казань, Санкт-Петербург"},
                    "birth_date": {"type": "string", "description": "Дата рождения в формате DD.MM.YYYY"},
                    "company": {"type": "string", "description": "Компания-работодатель. Примеры: Яндекс, Google, ASI Biont"},
                    "position": {"type": "string", "description": "Должность или роль. Примеры: разработчик, директор, менеджер"},
                    "skills": {"type": "string", "description": "Навыки и умения через запятую. Примеры: Python, React, управление проектами"},
                    "interests": {"type": "string", "description": "Интересы и увлечения через запятую. Примеры: ML, робототехника, бизнес"},
                    "goals": {"type": "string", "description": "Цели и планы. Примеры: запустить MVP, найти инвестора"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_task",
            "description": "Два сценария — РАЗНАЯ терминология:\n• ПОРУЧЕНИЕ АГЕНТУ (delegated_to_username = имя агента из блока КОМАНДА АГЕНТОВ) — подключает субагента. Дедлайн не обязателен. Называй ПОРУЧЕНИЕМ. ВАЖНО: поручай агенту только когда пользователь ПРОСИТ ДЕЙСТВИЕ, а не задаёт вопрос. На вопрос — ответь сам.\n• ДЕЛЕГИРОВАНИЕ ПОЛЬЗОВАТЕЛЮ (delegated_to_username = @username реального человека) — формальная задача с дедлайном другому пользователю. Требует согласия пользователя. Называй это ДЕЛЕГИРОВАНИЕМ.\nИмена агентов смотри в блоке КОМАНДА АГЕНТОВ в контексте.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Название задачи. Примеры: 'проверить документы', 'подготовить отчет'",
                    },
                    "delegated_to_username": {
                        "type": "string",
                        "description": "Имя получателя: username реального пользователя (без @) ИЛИ имя агента из блока КОМАНДА АГЕНТОВ в контексте",
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
            "description": "Принять делегированную задачу. Ключевые слова: 'принимаю', 'согласен', 'беру задачу'.",
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
            "description": "Отклонить делегированную задачу. Ключевые слова: 'отклоняю', 'не буду делать', 'отказываюсь'.",
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
            "name": "list_tasks",
            "description": "Показать все активные задачи пользователя. Вызывай ТОЛЬКО когда спрашивают о задачах ('мои задачи', 'список', 'что запланировано'). НЕ вызывай для создания, завершения или других операций с задачами.",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_completed": {
                        "type": "boolean",
                        "description": "true — показать в том числе завершённые"
                    },
                    "filter_type": {
                        "type": "string",
                        "description": "Фильтр: 'today' (на сегодня), 'overdue' (просроченные), 'delegated' (делегированные)",
                        "enum": ["today", "overdue", "delegated"]
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_contact_alert",
            "description": "Мониторинг контактов (все тарифы). Уведомит когда появится пользователь с нужными навыками/интересами.",
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
            "name": "web_search",
            "description": "🔎 Поиск в интернете для ИССЛЕДОВАНИЙ: тренды, новости, статьи, аналитика, сайты, сообщества, ресурсы. Возвращает результаты со ссылками. Для поиска ЛЮДЕЙ/контактов — используй свои интеграции (GitHub, email, RSS), НЕ web_search. Примеры: 'тренды AI-тестирования 2026', 'лучшие CRM для бизнеса', 'рынок AI-агентов Россия'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Поисковый запрос по теме. Примеры: 'рынок AI-агентов 2026', 'лучшие практики тестирования', 'анализ конкурентов SaaS'"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "research_topic",
            "description": "🔍 ПОИСК И АНАЛИЗ любой темы: тренды, конкуренты, конференции/ивенты/мероприятия, сообщества по теме, контакты организаторов. Единственный инструмент для поиска в интернете. Используй для: поиск ссылок/ресурсов, анализ рынка, тренды, стратегии, факты, цифры, поиск профессиональных событий и конференций. depth='basic' для быстрого поиска ссылок, 'full' для анализа, 'deep' для глубокого исследования.",
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
            "name": "schedule_background_task",
            "description": "⏳ Поставить фоновое исследование с отправкой результата пользователю через delay_minutes минут.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Тема для исследования. Конкретный поисковый запрос."
                    },
                    "reason": {
                        "type": "string",
                        "description": "Почему откладываешь: 'требует глубокого анализа', 'нужны свежие данные', 'займёт 15+ мин'"
                    },
                    "delay_minutes": {
                        "type": "integer",
                        "description": "Через сколько минут выполнить. По умолчанию 30.",
                        "default": 30
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_post",
            "description": "📝 Opубликовать пост в блог платформы (не TG-канал). ⚠️ СТРОГО: вызывай ТОЛЬКО если пользователь явно написал 'опубликуй', 'публикуй', 'запости'. Если пользователь просит 'подготовить черновик', 'написать текст', 'составь пост' — НЕ вызывай этот инструмент, просто показывай текст в чате. ВСЕГДА добавляй image_url с картинкой с Unsplash по теме поста.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Текст поста для блога. 2-3 абзаца, живой стиль, без маркетинговых клише. Пиши от лица пользователя."
                    },
                    "image_url": {
                        "type": "string",
                        "description": "URL картинки к посту. Используй Unsplash: https://source.unsplash.com/featured/?keyword1,keyword2 где keyword — тема поста на английском. Обязателен для каждого поста."
                    }
                },
                "required": ["content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_post",
            "description": "✏️ РЕДАКТИРОВАТЬ ПОСТ: Изменяет текст существующего поста в ленте. Если post_id не указан — редактирует последний пост. Ключевые слова: 'измени пост', 'исправь пост', 'отредактируй', 'перепиши пост'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "post_id": {
                        "type": "integer",
                        "description": "ID поста для редактирования. Если не указан — редактирует последний пост."
                    },
                    "new_content": {
                        "type": "string",
                        "description": "Новый текст поста"
                    }
                },
                "required": ["new_content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_posts",
            "description": "📋 ПОКАЗАТЬ МОИ ПОСТЫ: Список постов пользователя в ленте с датами, лайками, просмотрами. Ключевые слова: 'мои посты', 'покажи посты', 'что я публиковал', 'какие у меня посты'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Количество постов (по умолчанию 5, максимум 20)"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_post",
            "description": "🗑 УДАЛИТЬ ПОСТ: Удаляет пост пользователя из ленты. Ключевые слова: 'удали пост', 'убери пост', 'удали публикацию'. Если post_id не указан — удаляет последний пост.",
            "parameters": {
                "type": "object",
                "properties": {
                    "post_id": {
                        "type": "integer",
                        "description": "ID поста для удаления. Если не указан — удаляет последний пост пользователя."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_goal",
            "description": "🎯 Создать долгосрочную цель. Используй metric_target/metric_unit для числовых целей (50 учеников, 10 кг, 1 млн руб).",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Краткое название цели (2-8 слов)"
                    },
                    "description": {
                        "type": "string",
                        "description": "Подробное описание цели (опционально)"
                    },
                    "category": {
                        "type": "string",
                        "description": "Категория цели",
                        "enum": ["work", "personal", "health", "learning", "finance", "social"]
                    },
                    "priority": {
                        "type": "string",
                        "description": "Приоритет цели",
                        "enum": ["low", "medium", "high", "critical"]
                    },
                    "target_date": {
                        "type": "string",
                        "description": "Целевая дата достижения. Форматы: 'через 3 месяца', '2026-06-01', 'к лету'"
                    },
                    "success_criteria": {
                        "type": "string",
                        "description": "Как измерить успех. Примеры: 'сдать сертификацию', 'пробежать 42 км', 'выручка 1 млн'"
                    },
                    "metric_target": {
                        "type": "number",
                        "description": "Целевое числовое значение. Примеры: 50 (учеников), 10 (кг), 1000000 (руб). ОБЯЗАТЕЛЬНО когда цель измерима в цифрах"
                    },
                    "metric_unit": {
                        "type": "string",
                        "description": "Единица измерения метрики. Примеры: 'учеников', 'кг', 'руб', 'подписчиков', 'км', 'клиентов'"
                    }
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_goal",
            "description": "❌ УДАЛИТЬ ЦЕЛЬ: Удалить цель пользователя по названию или все цели сразу. Ключевые слова: 'удали цель', 'убери цель', 'удали все цели'. Примеры: 'удали цель тестирование' → delete_goal(goal_title='тестирование'), 'удали все цели' → delete_goal(goal_title='все')",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal_title": {
                        "type": "string",
                        "description": "Название или ключевые слова цели. 'все' или 'all' — удалить все цели."
                    }
                },
                "required": ["goal_title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_goal_progress",
            "description": "📊 ОБНОВИТЬ ПРОГРЕСС ЦЕЛИ: Изменить метрику, процент, статус или заметку. Если у цели есть метрика — используй metric_current (процент посчитается автоматически). Примеры: 'набрал 12 учеников' → update_goal_progress(goal_title='ученики', metric_current=12). 'сбросил 3 кг' → update_goal_progress(goal_title='похудеть', metric_current=3). 'прогресс 40%' → update_goal_progress(goal_title='...', progress=40)",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal_title": {
                        "type": "string",
                        "description": "Название или ключевые слова цели для поиска"
                    },
                    "progress": {
                        "type": "integer",
                        "description": "Процент выполнения (0-100). При 100% цель автоматически завершается. Игнорируется если указан metric_current (процент посчитается авто)"
                    },
                    "metric_current": {
                        "type": "number",
                        "description": "Текущее значение метрики. Примеры: 12 (учеников из 50), 3 (кг из 10), 250000 (руб из 1млн). Процент посчитается автоматически от metric_target"
                    },
                    "status": {
                        "type": "string",
                        "description": "Новый статус цели",
                        "enum": ["active", "completed", "paused", "cancelled"]
                    },
                    "notes": {
                        "type": "string",
                        "description": "Заметка о прогрессе (добавляется к истории)"
                    }
                },
                "required": ["goal_title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_goals",
            "description": "🎯 ПОКАЗАТЬ ЦЕЛИ: Список целей пользователя с прогрессом и статистикой. Ключевые слова: 'мои цели', 'покажи цели', 'какие у меня цели', 'прогресс'. Примеры: 'покажи мои цели' → list_goals(), 'завершённые цели' → list_goals(status_filter='completed')",
            "parameters": {
                "type": "object",
                "properties": {
                    "status_filter": {
                        "type": "string",
                        "description": "Фильтр по статусу: 'active' (активные), 'completed' (завершённые), 'all' (все)",
                        "enum": ["active", "completed", "paused", "all"]
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "publish_to_telegram",
            "description": "📢 Опубликовать пост в личный Telegram-канал. Ключевые слова: пост в канал, опубликуй в телеграм.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Текст поста для Telegram-канала. Поддерживает Markdown. Пиши качественный контент по теме канала."
                    },
                    "image_url": {
                        "type": "string",
                        "description": "URL картинки для прикрепления к посту. Получи его вызвав generate_image и взяв URL из ответа. Если не нужна картинка — не передавай."
                    }
                },
                "required": ["content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "publish_to_discord",
            "description": "📢 Опубликовать пост в Discord канал через webhook. Ключевые слова: пост в дискорд, опубликуй в discord.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Текст поста для Discord канала. Пиши качественный контент по теме."
                    },
                    "image_url": {
                        "type": "string",
                        "description": "URL картинки для embed. Получи его вызвав generate_image и взяв URL из ответа. Если не нужна картинка — не передавай."
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
            "description": "🎯 НАСТРОИТЬ СТРАТЕГИЮ КОНТЕНТА: Сохраняет стратегию контента для автономного ведения канала. AI будет автоматически генерировать и публиковать контент по этой стратегии. Примеры: 'хочу постить про свой бизнес', 'буду публиковать кейсы по дизайну'. ВАЖНО: Спроси детали — что постить, для кого, цель.",
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
            "name": "send_message_to_user",
            "description": "📩 Отправить сообщение другому пользователю платформы. Ключевые слова: напиши, предложи встречу, согласуй.",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient_username": {
                        "type": "string",
                        "description": "Username или имя получателя (без @). Примеры: 'ivan', 'anna_dev', 'Пётр'"
                    },
                    "intent": {
                        "type": "string",
                        "description": "Цель сообщения",
                        "enum": ["meeting", "collaboration", "idea", "project_invite", "question"]
                    },
                    "message_context": {
                        "type": "string",
                        "description": "Что хочет передать отправитель. В свободной форме. Примеры: 'хочу обсудить совместный проект по AI', 'предлагаю встретиться в пятницу в 14:00', 'ищу партнёра для стартапа'"
                    }
                },
                "required": ["recipient_username", "intent", "message_context"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_and_message_relevant_users",
            "description": "🔍🤝 Найти ПОЛЬЗОВАТЕЛЕЙ ПЛАТФОРМЫ по интересам/навыкам и отправить сообщение. Ищет только среди зарегистрированных пользователей ASI Biont. Если нужны внешние контакты — используй web_search + send_outreach_email. preview_only=true — показать без отправки.",
            "parameters": {
                "type": "object",
                "properties": {
                    "purpose": {
                        "type": "string",
                        "description": "Цель поиска. Описание кого ищем и зачем. Примеры: 'партнёр для стартапа по AI', 'кто бегает в Перми', 'дизайнер для проекта'"
                    },
                    "message_context": {
                        "type": "string",
                        "description": "Что предложить найденным людям. Примеры: 'предлагаю обсудить совместный проект', 'приглашаю на совместную пробежку', 'ищу сооснователя'"
                    },
                    "match_by": {
                        "type": "string",
                        "description": "По чему искать совпадения",
                        "enum": ["interests", "skills", "goals", "tasks", "city", "all"],
                        "default": "all"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Максимум человек (1-5)",
                        "default": 3
                    },
                    "preview_only": {
                        "type": "boolean",
                        "description": "true = только показать кого нашёл (без отправки). false/не указано = найти и отправить.",
                        "default": False
                    }
                },
                "required": ["purpose", "message_context"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "broadcast_message_to_all_users",
            "description": "📢 TELEGRAM-рассылка ВСЕМ пользователям платформы. Отправляет сообщение в Telegram каждому пользователю. Только для админа. ПРИОРИТЕТ: если пользователь говорит 'отправь всем', 'разошли всем пользователям', 'напиши всем', 'рассылка', 'broadcast' — используй ЭТОТ инструмент, а НЕ email-кампанию и НЕ find_and_message_relevant_users.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_text": {
                        "type": "string",
                        "description": "Текст сообщения для рассылки всем пользователям"
                    }
                },
                "required": ["message_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reply_to_user_message",
            "description": "💬 Ответить на сообщение от другого пользователя. Используй когда: 'ответь @ivan что согласен', 'напиши @anna ответ: давай в пятницу'. Ключевые слова: 'ответь', 'ответить', 'reply'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient_username": {
                        "type": "string",
                        "description": "Username того, кому отвечаем (без @)"
                    },
                    "reply_text": {
                        "type": "string",
                        "description": "Текст ответа"
                    }
                },
                "required": ["recipient_username", "reply_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_incoming_messages",
            "description": "📬 Показать входящие сообщения от других пользователей платформы.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status_filter": {
                        "type": "string",
                        "enum": ["unread", "all", "replied"],
                        "description": "Фильтр: unread (непрочитанные), all (все), replied (отвеченные)"
                    }
                },
                "required": []
            }
        }
    },
    # ═════ EMAIL — отправка писем конкретному адресату ═════
    {
        "type": "function",
        "function": {
            "name": "send_outreach_email",
            "description": "📤 Отправить персонализированное email конкретному адресату. Используй для outreach: пиши первое письмо конкретному человеку с персонализированным текстом.",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient_email": {
                        "type": "string",
                        "description": "Email получателя"
                    },
                    "recipient_name": {
                        "type": "string",
                        "description": "Имя получателя (для персонализации)"
                    },
                    "recipient_company": {
                        "type": "string",
                        "description": "Организация/проект получателя (если есть). Может быть компания, стартап, сообщество, канал, open-source проект"
                    },
                    "recipient_context": {
                        "type": "string",
                        "description": "Контекст получателя — почему пишем именно этому человеку. Примеры: 'Автор блога о маркетинге с 10k подписчиков', 'Фрилансер, ищет проекты для портфолио', 'Ведёт канал про продуктивность'"
                    },
                    "subject": {
                        "type": "string",
                        "description": "Тема письма — 4-8 слов, конкретная для ЭТОГО человека. ❌ ЗАПРЕЩЕНО: 'Testing', 'тест', 'тестирование', 'платформа', 'AI employee platform', 'возможность', 'предложение', 'opportunity', 'сотрудничество', названия кампаний, внутренняя терминология. ✅ ХОРОШО: ссылка на конкретный проект/статью/компанию получателя + связь с задачей. Пример: 'Ваш AI-проект X и агентная автоматизация', 'Federated Learning → точка пересечения'."
                    },
                    "body": {
                        "type": "string",
                        "description": "Текст письма — персонализированный. Пиши от первого лица, коротко (3-5 абзацев), с конкретным предложением и CTA. Первая строка — конкретная деталь о работе/проекте получателя (НЕ общий комплимент). Запрещено: 'Я был впечатлён', 'I was impressed by your work in [широкая область]'."
                    }
                },
                "required": ["recipient_email", "subject", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reply_to_outreach_email",
            "description": "💬 ОТВЕТИТЬ на входящее письмо от конкретного человека. Вызывается автономно при входящем reply или когда пользователь просит ответить.",
            "parameters": {
                "type": "object",
                "properties": {
                    "outreach_id": {
                        "type": "integer",
                        "description": "ID письма для ответа"
                    },
                    "recipient_email": {
                        "type": "string",
                        "description": "Email получателя (если outreach_id не указан)"
                    },
                    "reply_body": {
                        "type": "string",
                        "description": "Текст ответа — в рамках цели кампании, вежливый, с продвижением к следующему шагу. ⚠ КРИТИЧНО ЯЗЫК: если контакт ОТВЕТИЛ на определённом языке (reply_text) — отвечай на ЕГО языке! Если контакт пишет на греческом — ответ на греческом, на немецком — на немецком и т.д. Если reply_text отсутствует — пиши на языке оригинального outreach-письма. Если check_emails показал ⚠ ПРЕДПОЧТЕНИЯ КОНТАКТОВ — пиши на указанном языке."
                    }
                },
                "required": ["reply_body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_follow_up_email",
            "description": "📩 FOLLOW-UP EMAIL — повторное письмо получателю, который не ответил. Используй когда якорь email_follow_up сработал или пользователь просит отправить follow-up. Учитывай оригинальное письмо, чтобы не повторяться.",
            "parameters": {
                "type": "object",
                "properties": {
                    "outreach_id": {
                        "type": "integer",
                        "description": "ID письма для follow-up"
                    },
                    "recipient_email": {
                        "type": "string",
                        "description": "Email получателя (если outreach_id не указан)"
                    },
                    "subject": {
                        "type": "string",
                        "description": "Тема follow-up (по умолчанию: Re: оригинальная тема)"
                    },
                    "body": {
                        "type": "string",
                        "description": "Текст follow-up — коротко, ненавязчиво, с новой ценностью или вопросом"
                    }
                },
                "required": ["body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_emails",
            "description": "📬 Проверить входящие письма в почтовом ящике пользователя (Gmail/Яндекс/Mail.ru). Используй чтобы узнать кто написал, есть ли ответы на отправленные письма.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Количество последних писем (1-20, по умолчанию 5)"
                    },
                    "from_account": {
                        "type": "string",
                        "description": "Email-аккаунт, из которого читать (если несколько подключено). По умолчанию — первый доступный."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "📧 Отправить одиночное письмо с личной почты пользователя. Не связан с кампаниями. ВАЖНО: в теме и теле письма ВСЕГДА указывай контекст — название проекта, цель, суть предложения. Получатель должен понять о чём речь без дополнительного контекста.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Email получателя"
                    },
                    "subject": {
                        "type": "string",
                        "description": "Тема письма. Включай название проекта/компании/цель чтобы получатель понял контекст"
                    },
                    "body": {
                        "type": "string",
                        "description": "Текст письма. Пиши человечно: 3-4 абзаца, макс 150 слов. ОБЯЗАТЕЛЬНО укажи кто пишет, от какой компании/проекта, суть предложения. Подпись — твоё имя и должность."
                    },
                    "sender_name": {
                        "type": "string",
                        "description": "Имя отправителя. Передавай СВОЁ имя агента, если пользователь не попросил писать от его личного имени."
                    },
                    "from_account": {
                        "type": "string",
                        "description": "Email-адрес или название сервиса (gmail/yandex/mail.ru), с которого отправить. Указывай только если у пользователя несколько подключённых почт и он уточнил, с какой."
                    }
                },
                "required": ["to", "subject", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_email_contact",
            "description": "📇 СОХРАНИТЬ email-контакт в справочник пользователя. Вызывай когда нашёл email человека через IMAP/RSS/поиск или после получения ответа. Сохранённые контакты учитываются в прогрессе целей.",
            "parameters": {
                "type": "object",
                "properties": {
                    "email": {
                        "type": "string",
                        "description": "Email-адрес контакта. КОПИРУЙ ТОЧНО из источника (IMAP From, переписка). НИКОГДА не генерируй email из имени человека — транслитерация = ошибка."
                    },
                    "name": {
                        "type": "string",
                        "description": "Имя контакта"
                    },
                    "company": {
                        "type": "string",
                        "description": "Компания"
                    },
                    "position": {
                        "type": "string",
                        "description": "Должность"
                    },
                    "notes": {
                        "type": "string",
                        "description": "Заметки — почему контакт релевантен, что предлагали; предпочтения по общению (язык, стиль) в формате [предпочтение: язык: X] — сохраняются автоматически через check_emails"
                    },
                    "source": {
                        "type": "string",
                        "description": "Источник: manual / campaign / import / imap_reply / rss / github / web_search. При сохранении из GitHub search_users ставь 'github', при web_search — 'web_search'."
                    },
                    "status": {
                        "type": "string",
                        "description": "Статус: new / contacted / replied / interested / unsubscribed. По умолчанию replied (если человек сам написал)."
                    }
                },
                "required": ["email"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_email_contact_status",
            "description": "🔄 Обновить статус email-контакта. Используй когда контакт ответил негативно, попросил не писать (unsubscribed), или проявил интерес (interested). Автоматически отменяет follow-up при unsubscribed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "email": {
                        "type": "string",
                        "description": "Email контакта"
                    },
                    "status": {
                        "type": "string",
                        "description": "Новый статус: new / contacted / replied / interested / unsubscribed / bounced",
                        "enum": ["new", "contacted", "replied", "interested", "unsubscribed", "bounced"]
                    },
                    "reason": {
                        "type": "string",
                        "description": "Причина смены статуса (например: 'контакт попросил не писать на греческом')"
                    }
                },
                "required": ["email", "status"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_email_contacts",
            "description": "📋 СПИСОК email-контактов пользователя из справочника. Показывает все сохранённые контакты с их статусами.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status_filter": {
                        "type": "string",
                        "description": "Фильтр по статусу: all / new / contacted / replied / interested / bounced"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "negotiate_by_email",
            "description": "📨 Начать email-переговоры с итеративным диалогом до достижения цели. Используй вместо send_email когда нужна многошаговая переписка.",
            "parameters": {
                "type": "object",
                "properties": {
                    "contact_email": {
                        "type": "string",
                        "description": "Email контакта с которым ведём переговоры"
                    },
                    "contact_name": {
                        "type": "string",
                        "description": "Имя/ФИО контакта (опционально)"
                    },
                    "goal": {
                        "type": "string",
                        "description": (
                            "Цель переговоров — чего нужно достичь. "
                            "Примеры: 'Договориться о встрече', 'Согласовать условия партнёрства', "
                            "'Получить подтверждение заказа', 'Договориться об интервью'"
                        )
                    },
                    "opening_message": {
                        "type": "string",
                        "description": "Текст первого письма. Напиши развёрнуто — представься, объясни цель, задай конкретный вопрос."
                    },
                    "subject": {
                        "type": "string",
                        "description": "Тема письма. Если не указана — генерируется из goal."
                    },
                    "sender_name": {
                        "type": "string",
                        "description": "Имя отправителя (отображается в поле From). По умолчанию — имя пользователя."
                    },
                    "from_account": {
                        "type": "string",
                        "description": "С какого адреса отправить (email или часть адреса/метки). Если есть несколько интеграций."
                    }
                },
                "required": ["contact_email", "goal", "opening_message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "start_content_campaign",
            "description": "📝 Запустить автономную контент-кампанию: агент генерирует и публикует посты по расписанию. КРИТИЧНО: обязательно уточни время публикации у пользователя перед запуском.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Название кампании. Примеры: 'Ежедневные посты про AI', 'Серия про продуктивность', 'Контент для канала'"
                    },
                    "goal": {
                        "type": "string",
                        "description": "Подробная цель/стратегия — о чём писать, для кого, какой результат ожидается. Промпт для AI."
                    },
                    "topics": {
                        "type": "string",
                        "description": "Темы для постов (через ; или свободным текстом). Примеры: 'AI и автоматизация; продуктивность; нейросети в бизнесе'"
                    },
                    "platforms": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["feed", "telegram", "discord"]},
                        "description": "Площадки для публикации. feed = лента новостей, telegram = TG-канал, discord = Discord."
                    },
                    "tone": {
                        "type": "string",
                        "description": "Тон постов",
                        "enum": ["professional", "casual", "motivational", "expert", "friendly"],
                        "default": "professional"
                    },
                    "frequency": {
                        "type": "string",
                        "description": "Частота публикации",
                        "enum": ["daily", "every_2_days", "every_3_days", "weekly"],
                        "default": "daily"
                    },
                    "post_time": {
                        "type": "string",
                        "description": "Время публикации (HH:MM). ОБЯЗАТЕЛЬНО уточни у пользователя — не придумывай и не используй 12:00 по умолчанию. Если пользователь не указал время явно — спроси перед запуском."
                    },
                    "max_posts": {
                        "type": "integer",
                        "description": "Макс. количество постов (0 = без ограничений). По умолчанию 0."
                    }
                },
                "required": ["name", "goal", "platforms", "post_time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_content_campaign",
            "description": "⏸️ УПРАВЛЕНИЕ контент-кампанией: пауза, возобновление, отмена, обновление параметров. Используй когда: 'останови постинг', 'поставь кампанию на паузу', 'продолжи постить', 'отмени кампанию', 'измени расписание на раз в 2 дня', 'добавь тему X'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_id": {
                        "type": "integer",
                        "description": "ID контент-кампании. Если не указан — используется последняя активная."
                    },
                    "action": {
                        "type": "string",
                        "description": "Действие",
                        "enum": ["pause", "resume", "cancel", "update"]
                    },
                    "updates": {
                        "type": "object",
                        "description": "Обновляемые параметры (для action=update): goal, topics, tone, frequency, post_time, max_posts, platforms, name",
                        "properties": {
                            "name": {"type": "string"},
                            "goal": {"type": "string"},
                            "topics": {"type": "string"},
                            "tone": {"type": "string"},
                            "frequency": {"type": "string"},
                            "post_time": {"type": "string"},
                            "max_posts": {"type": "integer"},
                            "platforms": {"type": "array", "items": {"type": "string"}}
                        }
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "start_delegation_campaign",
            "description": "🤝 Запустить кампанию внешнего поиска исполнителей: агент находит реальных людей в интернете (не внутренних агентов) и рассылает им приглашения взять задачу. По сути — это аутрич-кампания: бот ищет подходящих людей, пишет им, они принимают или отклоняют. Когда отчитываться пользователю: называй это 'поиск [кого ищем]' или 'рассылка приглашений', не 'делегирование' — последнее звучит как внутреннее поручение. Ключевые слова: найди людей, найди исполнителей, разошли приглашения, аутрич.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Название кампании — любая задача, например: 'Копирайтеры для блога', 'Партнёры для коллаборации', 'Бета-тестеры для приложения'"
                    },
                    "goal": {
                        "type": "string",
                        "description": "Цель: что нужно сделать, зачем, какой результат ожидается"
                    },
                    "target_audience": {
                        "type": "string",
                        "description": "Кого ищем: навыки, интересы, опыт. Пример: 'маркетологи с опытом SMM', 'разработчики Python', 'дизайнеры UI/UX'"
                    },
                    "task_template": {
                        "type": "string",
                        "description": "Шаблон задачи: что конкретно должен сделать исполнитель"
                    },
                    "offer": {
                        "type": "string",
                        "description": "Мотивация: что получит исполнитель (опыт, обратная связь, коллаборация и т.д.)"
                    },
                    "tone": {
                        "type": "string",
                        "description": "Тон общения: professional, casual, friendly, motivational"
                    },
                    "max_delegations": {
                        "type": "integer",
                        "description": "Макс. количество делегирований (0=без ограничения, по умолчанию 10)"
                    },
                    "daily_limit": {
                        "type": "integer",
                        "description": "Макс. делегирований в день (по умолчанию 3)"
                    },
                    "default_deadline_hours": {
                        "type": "integer",
                        "description": "Дедлайн задачи в часах (по умолчанию 48)"
                    }
                },
                "required": ["name", "goal", "target_audience"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_delegation_campaign",
            "description": "⏸️ УПРАВЛЕНИЕ кампанией делегирования: пауза, возобновление, отмена, обновление. Используй когда: 'останови делегирование', 'поставь кампанию на паузу', 'отмени поиск исполнителей', 'измени лимит'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_id": {
                        "type": "integer",
                        "description": "ID кампании делегирования. Если не указан — последняя активная."
                    },
                    "action": {
                        "type": "string",
                        "description": "Действие",
                        "enum": ["pause", "resume", "cancel", "update"]
                    },
                    "updates": {
                        "type": "object",
                        "description": "Обновляемые параметры (для action=update): name, goal, target_audience, task_template, offer, tone, max_delegations, daily_limit, default_deadline_hours",
                        "properties": {
                            "name": {"type": "string"},
                            "goal": {"type": "string"},
                            "target_audience": {"type": "string"},
                            "task_template": {"type": "string"},
                            "offer": {"type": "string"},
                            "tone": {"type": "string"},
                            "max_delegations": {"type": "integer"},
                            "daily_limit": {"type": "integer"},
                            "default_deadline_hours": {"type": "integer"}
                        }
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "🎨 Сгенерировать изображение в нарисованном/иллюстративном стиле (Flux). Ключевые слова: нарисуй, создай изображение, сгенерируй картинку. Стиль всегда: hand-drawn illustration. Пиши промпт на английском.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Детальное описание изображения на английском языке. Чем подробнее — тем лучше результат. Пример: 'minimalist flat design illustration of a productive workspace with laptop, coffee and plants, soft teal and white color palette, clean modern aesthetic'"
                    },
                    "style": {
                        "type": "string",
                        "description": "Дополнительный стиль: photorealistic, illustration, minimalist, watercolor, 3d render, cinematic и т.п. Опционально."
                    },
                    "aspect_ratio": {
                        "type": "string",
                        "description": "Соотношение сторон: 1:1 (квадрат, для постов), 16:9 (горизонтальный, для баннеров), 9:16 (вертикальный, для stories). По умолчанию 1:1.",
                        "enum": ["1:1", "16:9", "9:16", "4:3", "3:4"]
                    }
                },
                "required": ["prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_status",
            "description": "ℹ️ CТАТУС СЕРВИСОВ — проверить работают ли все сервисы (емайл, новости, AI, погода, платёжка). Показывает активные ошибки, остаток квоты email (N/50 сегодня), баланс токенов. Используй когда: 'почему не отправляются письма', 'не работают новости', 'есть ли ошибки сейчас', 'есть ли проблемы с API', 'сколько писем осталось сегодня'.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reschedule_task",
            "description": "Перенести задачу на другое время. Используй когда пользователь говорит 'перенеси задачу', 'сдвинь на завтра', 'отложи на час' и т.п.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_title": {
                        "type": "string",
                        "description": "Название или часть названия задачи"
                    },
                    "new_time": {
                        "type": "string",
                        "description": "Новое время в формате ISO 8601 или естественном языке: 'завтра в 15:00', '2026-03-01T10:00'"
                    }
                },
                "required": ["task_title", "new_time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "restore_task",
            "description": "Восстановить удалённую или пропущенную задачу. Используй когда пользователь говорит 'верни задачу', 'восстанови', 'я передумал удалять'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_title": {
                        "type": "string",
                        "description": "Название или часть названия задачи"
                    },
                    "task_id": {
                        "type": "integer",
                        "description": "ID задачи (если известен)"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_task_details",
            "description": "Получить подробную информацию о конкретной задаче: описание, дедлайн, делегирование, статус. Используй когда спрашивают детали одной задачи.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_title": {
                        "type": "string",
                        "description": "Название или часть названия задачи"
                    },
                    "task_id": {
                        "type": "integer",
                        "description": "ID задачи (если известен)"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_time_conflicts",
            "description": "Проверить нет ли конфликтов в расписании на указанное время. Используй перед созданием задачи с конкретным временем или постановкой напоминания.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reminder_time": {
                        "type": "string",
                        "description": "Время для проверки: ISO 8601 или человекочитаемое 'завтра в 14:00'"
                    }
                },
                "required": ["reminder_time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_delegation",
            "description": "Отменить делегирование задачи. Задача вернётся к пользователю.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "integer",
                        "description": "ID делегированной задачи"
                    }
                },
                "required": ["task_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather_info",
            "description": "🌤 Получить погоду в указанном городе с практическими рекомендациями. Используй когда пользователь спрашивает о погоде или планирует выход на улицу.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "Город. Примеры: 'Москва', 'Санкт-Петербург', 'Дубай', 'New York'"
                    }
                },
                "required": ["city"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_price",
            "description": "📈 Получить котировку акции, цену нефти, курс валюты или криптовалюты через Alpha Vantage. Работает только если у агента настроен ALPHAVANTAGE_API_KEY. Нефть: symbol=BRENT или WTI, data_type=oil. Акции: AAPL, MSFT, TSLA. Форекс: EUR/USD, USD/RUB. Крипто: BTC, ETH.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Тикер или пара. Примеры: 'AAPL', 'MSFT', 'EUR/USD', 'USD/RUB', 'XAU/USD', 'BTC'"
                    },
                    "data_type": {
                        "type": "string",
                        "description": "Тип данных: 'quote' (акции/ETF), 'forex' (валюты/металлы), 'crypto' (криптовалюта)",
                        "enum": ["quote", "forex", "crypto"]
                    }
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_news_trends",
            "description": "📰 Получить актуальные новости и тренды по теме. Используй когда нужна подборка новостей для анализа рынка, контент-плана или просто «что происходит в нише».",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Тема для поиска, например 'AI стартапы', 'крипторынок', 'маркетинг 2026'"
                    },
                    "period": {
                        "type": "string",
                        "description": "Период: 'day' (сутки), 'week' (неделя), 'month' (месяц)",
                        "enum": ["day", "week", "month"]
                    },
                    "focus": {
                        "type": "string",
                        "description": "Фокус анализа: 'trends' (тренды), 'news' (новости), 'insights' (инсайты для бизнеса)",
                        "enum": ["trends", "news", "insights"]
                    }
                },
                "required": ["topic"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "quick_topic_search",
            "description": "⚡ Быстрый поиск краткой справки по теме (3-5 предложений). Используй для быстрых фактических вопросов, определений, числовых данных. Для глубокого анализа — используй research_topic.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Тема или вопрос для быстрого поиска"
                    }
                },
                "required": ["topic"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "research_and_plan",
            "description": "🧠 Исследовать тему И сразу составить план действий. Используй когда пользователь хочет не только узнать о чём-то, но и понять что делать дальше. Возвращает анализ + конкретные шаги.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Запрос для исследования. Примеры: 'как запустить SaaS продукт в 2026', 'продвижение в B2B без бюджета'"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_situation_and_suggest_tasks",
            "description": "🔭 Полный анализ текущей ситуации пользователя: задачи, цели, активность — и предложение конкретных задач для движения вперёд. Используй когда пользователь говорит 'что мне делать', 'помоги разобраться', 'застрял', 'не знаю с чего начать'.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_group_opportunities",
            "description": "Найти потенциальных партнёров, клиентов или коллаборации среди других пользователей платформы на основе совпадения интересов, целей и навыков.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_partners",
            "description": "Найти партнёров / соисполнителей / клиентов для конкретной задачи или проекта среди пользователей платформы. Используй когда пользователь ищет команду, партнёра или подрядчика.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_marketing_content",
            "description": "✍️ Создать маркетинговый контент для продукта/услуги: пост, объявление, pitch, описание. Используй когда нужен готовый текст для продвижения.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_name": {
                        "type": "string",
                        "description": "Название продукта или услуги"
                    },
                    "target_audience": {
                        "type": "string",
                        "description": "Целевая аудитория, например 'малый бизнес', 'фрилансеры', 'стартапы'"
                    },
                    "platform": {
                        "type": "string",
                        "description": "Площадка: 'telegram', 'instagram', 'linkedin', 'email', 'website'",
                        "enum": ["telegram", "instagram", "linkedin", "email", "website", "vk"]
                    },
                    "goal": {
                        "type": "string",
                        "description": "Цель контента: 'привлечение', 'конверсия', 'удержание', 'awareness'",
                        "enum": ["привлечение", "конверсия", "удержание", "awareness"]
                    }
                },
                "required": ["product_name", "target_audience", "platform"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_message_status",
            "description": "Проверить статус отправленного сообщения другому пользователю платформы: доставлено ли, прочитано, есть ли ответ.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": "Установить напоминание для задачи или произвольное напоминание на конкретное время. Используй когда пользователь говорит 'напомни мне', 'поставь напоминание'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reminder_text": {
                        "type": "string",
                        "description": "Текст напоминания или название задачи"
                    },
                    "reminder_time": {
                        "type": "string",
                        "description": "Время напоминания: ISO 8601 или 'завтра в 10:00', 'через 2 часа', '1 марта в 9:00'"
                    }
                },
                "required": ["reminder_text", "reminder_time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_goal",
            "description": "Обновить параметры цели: название, описание, дедлайн, статус. Используй когда пользователь хочет изменить существующую цель.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal_id": {
                        "type": "integer",
                        "description": "ID цели (если известен)"
                    },
                    "title": {
                        "type": "string",
                        "description": "Новое название цели"
                    },
                    "description": {
                        "type": "string",
                        "description": "Новое описание"
                    },
                    "target_date": {
                        "type": "string",
                        "description": "Новый дедлайн в формате ISO 8601 или 'через 3 месяца'"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "complete_goal",
            "description": "Отметить цель как выполненную. Используй когда пользователь говорит 'цель достигнута', 'выполнил цель', 'закрой цель'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal_id": {
                        "type": "integer",
                        "description": "ID цели (если известен)"
                    },
                    "title": {
                        "type": "string",
                        "description": "Название цели (если ID не известен)"
                    }
                }
            }
        }
    },
    # ─── MARKETPLACE ──────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "list_marketplace",
            "description": "🛒 МАРКЕТПЛЕЙС — показать список агентов или скриптов. Используй когда: 'что есть в маркетплейсе', 'покажи агентов', 'какие скрипты доступны', 'найди агента по маркетингу'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_type": {
                        "type": "string",
                        "enum": ["agents", "scripts"],
                        "description": "Что показать: agents (агенты) или scripts (скрипты). По умолчанию agents."
                    },
                    "category": {
                        "type": "string",
                        "description": "Фильтр по категории: marketing, legal, finance, dev, lifestyle, analytics, misc"
                    },
                    "search": {
                        "type": "string",
                        "description": "Поиск по названию"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "switch_agent",
            "description": "🤖 ПЕРЕКЛЮЧИТЬСЯ НА АГЕНТА — подключиться к кастомному агенту из маркетплейса или вернуться к стандартному ASI Biont. Используй когда: 'хочу поговорить с @slug', 'подключи агента крипто', 'вернись в обычный режим', 'переключись на ASI Biont'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_slug": {
                        "type": "string",
                        "description": "Slug агента (например 'crypto-alex' или '@crypto-alex'). Не нужен при reset=true."
                    },
                    "reset": {
                        "type": "boolean",
                        "description": "true — вернуться к стандартному ASI Biont"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_agent_action",
            "description": "⚡ Выполнить действие через подключённую интеграцию агента (любые внешние API и сервисы). Используй творчески — одна интеграция может решать разные задачи.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Название действия (должно совпадать с тем, что обрабатывает скрипт агента). Примеры: send_message, create_issue, create_card, add_row, create_record. Для email используй send_email или send_outreach_email напрямую (НЕ через run_agent_action)."
                    },
                    "params": {
                        "type": "object",
                        "description": "Параметры действия. Передаются в скрипт через переменные окружения AGENT_PARAM_КЛЮЧ. Пример: {\"message\": \"...\", \"channel\": \"...\", \"title\": \"...\"}"
                    }
                },
                "required": ["action"]
            }
        }
    },
]
