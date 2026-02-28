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
    'get_weather_info',                  # дубль встроенного контекста погоды
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
    'web_search',                        # дубль research_topic (поиск + анализ)
    'and_',                              # SQLAlchemy operator leak
    'or_',                               # SQLAlchemy operator leak
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
            "description": "🎯 Найти контакты для задачи/активности. Вызывай после add_task если задача связана с людьми (спорт, бизнес, обучение).",
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
            "description": "Создать НОВУЮ задачу. Явные запросы (напомни, поставь задачу, создай, запланируй) с указанным временем → СРАЗУ создавай БЕЗ переспрашивания. Неявные упоминания событий с временем (встреча в 15:00, дедлайн завтра, звонок после обеда) → сначала проверь get_tasks, если задачи нет — создай, ставь напоминание за 15 мин до события. Если время НЕ указано — СПРОСИ. Для ИЗМЕНЕНИЯ существующей задачи — ВСЕГДА edit_task, НЕ add_task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Краткое название задачи (2-5 слов). Извлекай суть из запроса пользователя.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Краткие детали задачи (макс 150 символов). 1-2 коротких предложения, суть без лишних слов. НЕ дублируй название.",
                    },
                    "reminder_time": {
                        "type": "string",
                        "description": "Время напоминания. Если время есть в запросе — используй сразу. Если нет — спроси. Форматы: 'завтра в 9:00', 'через 2 часа', 'YYYY-MM-DD HH:MM'.",
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
            "name": "complete_task",
            "description": "Отметить задачу выполненной. ОБЯЗАТЕЛЬНО заполняй completion_note — запиши что получилось, результат, вывод. Если пользователь не сказал результат — спроси перед вызовом. Ключевые слова: 'сделал', 'готово', 'завершил', 'закрыть', 'проверил', 'уже', 'разобрался', 'прочитал', 'написал', 'отправил', 'посмотрел', 'доделал'. task_title опционален если есть задача в фокусе. СТРОГО 1 вызов на 1 задачу — НЕ вызывай повторно если задача уже завершена.",
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
            "description": "ИЗМЕНИТЬ СУЩЕСТВУЮЩУЮ ЗАДАЧУ (название, описание, время). Вызывай когда пользователь хочет изменить текст или время задачи, ИЛИ когда пользователь ДОПОЛНЯЕТ недавно созданную задачу (например, указывает время после создания). Ключевые слова: 'измени', 'исправь', 'обнови', 'перенеси', 'переназначь', 'через N минут/часов', 'давай в ...', 'на ... поставь'. Примеры: 'измени название задачи на X' → edit_task, 'перенеси задачу на завтра' → edit_task(reminder_time='завтра в 10:00'), 'добавь описание' → edit_task, 'через 20 минут' (после создания задачи) → edit_task(reminder_time='через 20 минут'). ЕСЛИ задача УЖЕ СОЗДАНА — используй edit_task, а НЕ add_task.",
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
            "description": "Делегировать задачу другому пользователю. Используй когда видишь 'делегируй', 'поручи', 'передай'. Примеры: 'делегируй Ивану X', 'поручи @maria X завтра в 10:00'.",
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
            "description": "🔎 ПРЯМОЙ поиск в Google — возвращает результаты С КЛИКАБЕЛЬНЫМИ ССЫЛКАМИ. Используй когда пользователю нужны конкретные ресурсы, сайты, чаты, каналы, сервисы, контакты, места — всё, где важны ССЫЛКИ для перехода. Примеры: 'бизнес-чаты в Telegram', 'курсы по маркетингу', 'CRM для малого бизнеса'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Поисковый запрос. Примеры: 'лучшие бизнес-чаты Telegram 2026', 'CRM системы для малого бизнеса'"
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
            "description": "🔍 ПОИСК И АНАЛИЗ любой темы. Единственный инструмент для поиска информации в интернете. Используй для: поиск ссылок/ресурсов, анализ рынка, тренды, стратегии, факты, цифры. depth='basic' для быстрого поиска ссылок, 'full' для анализа, 'deep' для глубокого исследования.",
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
            "description": "⏳ Поставить себе фоновую задачу-исследование. Используй когда вопрос требует времени (15+ мин), пользователь занят и не ждёт ответа прямо сейчас, или когда лучше изучить тему глубже и прислать полный результат позже. Агент сам выполнит исследование через delay_minutes минут и СРАЗУ пришлёт результат пользователю в Telegram. После вызова — скажи пользователю что именно изучишь и когда пришлёшь.",
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
            "description": "📝 ПОСТ В ЛЕНТУ НОВОСТЕЙ ПЛАТФОРМЫ (НЕ в TG-канал!): Создаёт пост от имени пользователя в ОБЩУЮ ЛЕНТУ НОВОСТЕЙ на платформе — её видят ВСЕ пользователи. Это основной инструмент публикации. ВАЖНО: публикуй ТОЛЬКО после согласия пользователя. Флоу: 1) предложи тему → 2) получи 'ок/давай/да' → 3) create_post. Ключевые слова: 'опубликуй', 'запости', 'пост', 'пост в ленту', 'да, публикуй', 'ок, давай'. По умолчанию, когда пользователь говорит 'сделай пост' — это ЛЕНТА, не TG-канал.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Текст поста для ленты новостей. 2-3 абзаца, живой стиль, без маркетинговых клише. Пиши от лица пользователя."
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
            "description": "🎯 СОЗДАТЬ ЦЕЛЬ: Долгосрочная цель с измеримым прогрессом. Если цель имеет числовую метрику (набрать 50 учеников, похудеть на 10 кг, заработать 1млн) — ИСПОЛЬЗУЙ metric_target и metric_unit. Если метрика не очевидна — СПРОСИ 'в чём измеряем успех?' Примеры: 'набрать 50 учеников' → create_goal(title='Набрать учеников на курс', metric_target=50, metric_unit='учеников'). 'похудеть на 10 кг' → metric_target=10, metric_unit='кг'. ПОСЛЕ создания предложи разбить на задачи!",
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
            "name": "get_stock_info",
            "description": "📈 Котировки и анализ любых финансовых активов через поиск + AI. Акции, сырьё (нефть, золото), криптовалюты, индексы. Вызывай когда спрашивают про цену, курс, котировки любого актива. Возвращает текущую цену, изменение, ключевые факторы и экспертный вывод. Для ГЛУБОКОГО рыночного анализа (тренды, прогнозы, стратегии) — используй research_topic.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Тикер или название актива. Примеры: 'AAPL' (Apple), 'GOOGL' (Google), 'SBER' (Сбербанк), 'Brent oil', 'Gold price', 'Bitcoin', 'EUR/USD', 'S&P 500'"
                    }
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "publish_to_telegram",
            "description": "📢 ПОСТ В TELEGRAM КАНАЛ (НЕ в ленту!): Публикует пост в личный Telegram-канал пользователя. АВТОМАТИЧЕСКОЕ ПРАВИЛО: перед публикацией ВСЕГДА сначала вызывай generate_image и передавай URL в image_url. Если generate_image вернул ошибку — публикуй без image_url, пост должен выйти в любом случае. Ключевые слова: 'пост в канал', 'опубликуй в телеграм', 'запости в мой канал'.",
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
            "description": "📢 ПОСТ В DISCORD КАНАЛ: Публикует пост в Discord канал пользователя через webhook. АВТОМАТИЧЕСКОЕ ПРАВИЛО: перед публикацией ВСЕГДА сначала вызывай generate_image и передавай URL в image_url. Если generate_image вернул ошибку — публикуй без image_url, пост должен выйти в любом случае. Ключевые слова: 'пост в дискорд', 'опубликуй в discord'.",
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
            "description": "📩 Отправить сообщение другому пользователю через AI-агента. Используй когда пользователь просит: 'напиши Ивану', 'предложи @anna встречу', 'согласуй с @petrov время'. AI генерирует вежливое персонализированное сообщение. НЕ путай с delegate_task — здесь нет задачи, только общение.",
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
            "description": "🔍🤝 Найти релевантных пользователей и отправить им сообщение. Ищет людей с похожими интересами, навыками, целями или задачами. Используй когда: 'найди кто тоже занимается AI', 'хочу найти партнёра для бега', 'кто в Москве интересуется стартапами'. ОТЛИЧИЕ от find_relevant_contacts_for_task: здесь агент не просто ищет, а ОТПРАВЛЯЕТ сообщение найденным людям.",
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
                    }
                },
                "required": ["purpose", "message_context"]
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
            "description": "📬 Показать входящие сообщения от других пользователей. Вызывай проактивно когда в контексте есть НЕПРОЧИТАННЫХ СООБЩЕНИЙ или пользователь спрашивает про сообщения.",
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
    # ═════ EMAIL OUTREACH — автономные email-кампании (любая цель) ═════
    {
        "type": "function",
        "function": {
            "name": "start_email_campaign",
            "description": "📧 ЗАПУСТИТЬ EMAIL-КАМПАНИЮ для ЛЮБОЙ цели: привлечение клиентов, поиск тестировщиков, приглашение на мероприятие, нетворкинг, сотрудничество, набор команды — любой email-аутрич. AI-агент автономно найдёт контакты через веб-поиск, напишет персонализированные письма, отправит через Resend и ответит на входящие. Используй когда: 'найди тестировщиков', 'напиши предложение', 'запусти email-рассылку', 'пригласи людей', 'найди партнёров'. СНАЧАЛА спроси: кого ищем, зачем, что предлагаем.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Название кампании. Примеры: 'Поиск тестировщиков для бета-теста', 'Приглашение на митап', 'Нетворкинг с разработчиками'"
                    },
                    "goal": {
                        "type": "string",
                        "description": "Подробная цель — что хотим добиться. Примеры: 'Найти 10 бета-тестеров для AI-приложения', 'Пригласить 20 спикеров на конференцию', 'Найти партнёров для совместного проекта'"
                    },
                    "target_audience": {
                        "type": "string",
                        "description": "Описание целевой аудитории — кого ищем. Примеры: 'Тестировщики и ранние пользователи AI-продуктов', 'Фрилансеры-дизайнеры', 'Инди-разработчики Telegram-ботов'"
                    },
                    "offer": {
                        "type": "string",
                        "description": "Что предлагаем — может быть продукт, возможность, приглашение, идея. Примеры: 'Бесплатный ранний доступ к AI-ассистенту', 'Участие в хакатоне с призами', 'Коллаборация над open-source проектом'"
                    },
                    "sender_name": {
                        "type": "string",
                        "description": "Имя отправителя (как подписываться в письмах). По умолчанию — имя пользователя."
                    },
                    "sender_email": {
                        "type": "string",
                        "description": "Email отправителя (должен быть верифицирован в Resend). По умолчанию: outreach@asibiont.com"
                    },
                    "tone": {
                        "type": "string",
                        "description": "Тон писем",
                        "enum": ["professional", "friendly", "formal"],
                        "default": "professional"
                    },
                    "max_emails": {
                        "type": "integer",
                        "description": "Макс. количество писем в кампании. 0 = безлимит. По умолчанию 0 (безлимит).",
                        "default": 0
                    },
                    "daily_limit": {
                        "type": "integer",
                        "description": "Макс. писем в день. По умолчанию 50 — это максимум, ВСЕГДА ставь 50 если пользователь не указал другое.",
                        "default": 50
                    }
                },
                "required": ["name", "goal", "target_audience", "offer"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_email_campaign",
            "description": "✏️ ОБНОВИТЬ ПАРАМЕТРЫ существующей email-кампании: daily_limit, max_emails, name, goal, target_audience, offer, tone, status. Используй ВМЕСТО создания новой кампании, когда пользователь просит изменить настройки: 'измени лимит на 50', 'поставь на паузу', 'обнови цель кампании'. НЕ создавай новую кампанию — обнови существующую!",
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_id": {
                        "type": "integer",
                        "description": "ID кампании для обновления. Если не указан — обновляется последняя активная."
                    },
                    "name": {
                        "type": "string",
                        "description": "Новое название кампании"
                    },
                    "goal": {
                        "type": "string",
                        "description": "Новая цель кампании"
                    },
                    "target_audience": {
                        "type": "string",
                        "description": "Новое описание целевой аудитории"
                    },
                    "offer": {
                        "type": "string",
                        "description": "Новое предложение"
                    },
                    "tone": {
                        "type": "string",
                        "description": "Тон писем",
                        "enum": ["professional", "friendly", "formal"]
                    },
                    "max_emails": {
                        "type": "integer",
                        "description": "Новый лимит писем всего (0 = безлимитно)"
                    },
                    "daily_limit": {
                        "type": "integer",
                        "description": "Новый дневной лимит писем (1-50)"
                    },
                    "status": {
                        "type": "string",
                        "description": "Новый статус кампании",
                        "enum": ["active", "paused", "completed", "cancelled"]
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_outreach_email",
            "description": "📤 ОТПРАВИТЬ EMAIL в рамках кампании. Используй после web_search когда нашёл контактный email нужного человека. AI пишет персонализированное письмо с учётом цели кампании и контекста получателя. Получатель — не обязательно компания: может быть разработчик, блогер, тестировщик, спикер, любой человек. Также вызывается автономно якорем email_outreach_send.",
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_id": {
                        "type": "integer",
                        "description": "ID кампании (если не указан, берётся последняя активная)"
                    },
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
                        "description": "Почему этот контакт релевантен кампании. Примеры: 'Автор Telegram-бота с 5k пользователей', 'Тестировщик, ищет проекты для портфолио', 'Ведёт канал про AI-инструменты'"
                    },
                    "subject": {
                        "type": "string",
                        "description": "Тема письма — цепляющая, персонализированная"
                    },
                    "body": {
                        "type": "string",
                        "description": "Текст письма — персонализированный, с учётом цели кампании и контекста получателя. Пиши от первого лица, коротко (3-5 абзацев), с конкретным предложением и CTA."
                    }
                },
                "required": ["recipient_email", "subject", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_email_leads",
            "description": "📋 ДОБАВИТЬ EMAIL-АДРЕСА в кампанию. Используй после web_search, когда нашёл email-адреса нужных людей (клиенты, тестировщики, партнёры, спикеры — любая аудитория). Принимает JSON-массив или список email через запятую.",
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_id": {
                        "type": "integer",
                        "description": "ID кампании (если не указан, берётся последняя активная)"
                    },
                    "leads": {
                        "type": "string",
                        "description": "Email-адреса: JSON [{\"email\":\"a@b.com\",\"name\":\"Name\",\"company\":\"Co\",\"context\":\"why\"}] или через запятую: a@b.com, c@d.com"
                    }
                },
                "required": ["leads"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reply_to_outreach_email",
            "description": "💬 ОТВЕТИТЬ на входящее письмо от получателя в рамках email-кампании. AI автоматически формирует ответ в рамках цели кампании. Вызывается автономно при входящем reply или когда пользователь просит ответить.",
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
                        "description": "Текст ответа — в рамках цели кампании, вежливый, с продвижением к следующему шагу"
                    }
                },
                "required": ["reply_body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_email_campaign_status",
            "description": "📊 СТАТУС EMAIL-КАМПАНИИ: сколько отправлено, ответов, ошибок. Вызывай когда: 'статус email', 'как кампания', 'сколько ответов'. Также вызывается проактивно при наличии новых ответов.",
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_id": {
                        "type": "integer",
                        "description": "ID кампании (если не указан, покажет все)"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "pause_email_campaign",
            "description": "⏸️ ПАУЗА/ВОЗОБНОВЛЕНИЕ email-кампании. Управляет статусом: pause (остановить), resume (продолжить), cancel (отменить).",
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_id": {
                        "type": "integer",
                        "description": "ID кампании"
                    },
                    "action": {
                        "type": "string",
                        "description": "Действие",
                        "enum": ["pause", "resume", "cancel"]
                    }
                },
                "required": ["action"]
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
            "name": "send_email",
            "description": "📧 ОТПРАВИТЬ EMAIL — универсальный инструмент. Отправь письмо на любой адрес: предложение, вопрос, напоминание, благодарность, приглашение, что угодно. НЕ связан с кампаниями — одиночная отправка. Используй когда пользователь даёт email и просит написать/отправить что-то конкретное.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Email получателя"
                    },
                    "subject": {
                        "type": "string",
                        "description": "Тема письма"
                    },
                    "body": {
                        "type": "string",
                        "description": "Текст письма. Пиши человечно: 3-4 абзаца, макс 150 слов, без шаблонных фраз"
                    },
                    "sender_name": {
                        "type": "string",
                        "description": "Имя отправителя (по умолчанию — имя пользователя)"
                    },
                    "sender_email": {
                        "type": "string",
                        "description": "Email отправителя (по умолчанию — outreach@asibiont.com)"
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
            "description": "📇 СОХРАНИТЬ email-контакт в справочник пользователя. Вызывай когда пользователь даёт email для будущих рассылок или после отправки письма.",
            "parameters": {
                "type": "object",
                "properties": {
                    "email": {
                        "type": "string",
                        "description": "Email-адрес контакта"
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
                        "description": "Заметки — почему контакт релевантен"
                    },
                    "source": {
                        "type": "string",
                        "description": "Источник: manual / campaign / import"
                    }
                },
                "required": ["email"]
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
            "name": "start_content_campaign",
            "description": "📝 ЗАПУСТИТЬ КОНТЕНТ-КАМПАНИЮ — автономная публикация постов по расписанию в ленту, Telegram-канал и/или Discord. Агент сам генерирует и публикует контент. Используй когда пользователь ЯВНО дал команду запустить (\"запусти\", \"создай\", \"давай\", \"ок поехали\"). Если пользователь выразил лишь ИНТЕРЕС (\"интересная идея\", \"звучит хорошо\") — НЕ вызывай, сначала уточни одним сообщением: (1) о чём постить, (2) куда (лента/TG/Discord), (3) в какое время — предложи конкретное HH:MM. post_time ВСЕГДА согласовывай с пользователем, не используй дефолтный 12:00 молча.",
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
                        "description": "Предпочтительное время публикации (HH:MM). По умолчанию 12:00"
                    },
                    "max_posts": {
                        "type": "integer",
                        "description": "Макс. количество постов (0 = без ограничений). По умолчанию 0."
                    }
                },
                "required": ["name", "goal", "platforms"]
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
            "description": "🤝 ЗАПУСК КАМПАНИИ ДЕЛЕГИРОВАНИЯ — автономное массовое распределение задач подходящим пользователям. Агент сам найдёт исполнителей по навыкам/интересам, создаст задачи, отправит уведомления. Используй когда: 'найди тестировщиков для проекта', 'раздай задачи', 'найди помощников', 'делегируй командой', 'распредели задачи'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Название кампании: 'Тестировщики для MVP', 'Помощники по дизайну'"
                    },
                    "goal": {
                        "type": "string",
                        "description": "Цель: что нужно сделать, зачем, какой результат ожидается"
                    },
                    "target_audience": {
                        "type": "string",
                        "description": "Кого ищем: навыки, интересы, опыт. Пример: 'тестировщики, QA, разработчики Python'"
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
            "description": "🎨 ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЯ через Replicate (Flux). Создаёт картинку по текстовому описанию и отправляет пользователю в Telegram. Используй когда просят: 'нарисуй', 'создай изображение', 'сгенерируй картинку', 'сделай иллюстрацию для поста', 'визуал для кампании'. Пиши подробный английский промпт для лучшего качества. ВАЖНО: картинка автоматически вставляется в ответ — ты её не вставляй отдельно.",
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
            "name": "install_script",
            "description": "📦 УСТАНОВИТЬ СКРИПТ из маркетплейса. Используй когда: 'установи скрипт', 'подключи модуль', 'хочу использовать скрипт X'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "script_slug": {
                        "type": "string",
                        "description": "Slug скрипта из маркетплейса"
                    },
                    "script_id": {
                        "type": "integer",
                        "description": "ID скрипта (альтернатива slug)"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_user_script",
            "description": "▶️ ЗАПУСТИТЬ СКРИПТ — выполнить установленный скрипт из маркетплейса. Используй когда пользователь просит запустить/использовать конкретный скрипт. Скрипт должен быть предварительно установлен.",
            "parameters": {
                "type": "object",
                "properties": {
                    "script_slug": {
                        "type": "string",
                        "description": "Slug скрипта"
                    },
                    "script_id": {
                        "type": "integer",
                        "description": "ID скрипта (альтернатива slug)"
                    },
                    "params": {
                        "type": "object",
                        "description": "Параметры для скрипта — JSON-объект с входными данными согласно описанию скрипта"
                    }
                }
            }
        }
    },
]
