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
            "name": "check_time_conflicts",
            "description": "Проверить свободно ли время. НЕ вызывай перед add_task — add_task сам проверяет конфликты. Используй ТОЛЬКО если пользователь спрашивает о свободном времени БЕЗ создания задачи.",
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
            "description": "🔍 ГЛУБОКИЙ АНАЛИЗ темы с AI-экспертизой. Используй когда нужен АНАЛИТИЧЕСКИЙ разбор: тренды рынка, стратегии, сравнение подходов, экспертные выводы. НЕ используй если пользователю нужны просто ссылки/ресурсы — для этого есть web_search.",
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
            "name": "get_news_trends",
            "description": "📰 Новости и тренды по теме. Вызывай при вопросах о новостях/трендах.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Тема для поиска новостей. Примеры: 'AI', 'бизнес', 'технологии', 'стартапы', 'финансы'"
                    },
                    "period": {
                        "type": "string",
                        "description": "Период времени: 'today' (сегодня), 'week' (неделя), 'month' (месяц)",
                        "default": "week",
                        "enum": ["today", "week", "month"]
                    },
                    "focus": {
                        "type": "string",
                        "description": "Фокус анализа: 'news' (новости), 'trends' (тренды), 'opportunities' (возможности), 'business' (бизнес)",
                        "default": "trends",
                        "enum": ["news", "trends", "opportunities", "business"]
                    }
                },
                "required": ["topic"]
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
            "description": "� Котировки и анализ любых финансовых активов через Serper + AI. Акции, сырьё (нефть, золото), криптовалюты, индексы. Вызывай когда спрашивают про цену, курс, котировки любого актива. Возвращает текущую цену, изменение, ключевые факторы и экспертный вывод. Для ГЛУБОКОГО рыночного анализа (тренды, прогнозы, стратегии) — используй research_topic.",
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
            "name": "skip_task",
            "description": "ПРОПУСТИТЬ ЗАДАЧУ: Помечает задачу как пропущенную (не выполнена, не удалена). Для случаев когда задача неактуальна но удалять не хочется. Ключевые слова: 'пропустить', 'пропусти задачу', 'неактуально'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "integer",
                        "description": "ID задачи для пропуска (опционально если есть task_title)"
                    },
                    "task_title": {
                        "type": "string",
                        "description": "Ключевые слова задачи для пропуска (опционально если есть task_id)"
                    },
                    "reason": {
                        "type": "string",
                        "description": "Причина пропуска (почему неактуальна, что помешало)"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "restore_task",
            "description": "♻️ ВОССТАНОВИТЬ ЗАДАЧУ: Возвращает завершённую или пропущенную задачу обратно в pending. Ключевые: 'верни задачу', 'восстанови', 'задача снова актуальна'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "integer",
                        "description": "ID задачи для восстановления (опционально если есть task_title)"
                    },
                    "task_title": {
                        "type": "string",
                        "description": "Ключевые слова задачи для восстановления (опционально если есть task_id)"
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
            "description": "📢 ПОСТ В TELEGRAM КАНАЛ (НЕ в ленту!): Публикует пост в личный Telegram-канал пользователя. Это ДРУГАЯ система, не путай с create_post (лента новостей). Используй когда пользователь явно просит опубликовать в его TG-канал. Требует: канал должен быть указан в профиле, бот добавлен как админ канала. Ключевые слова: 'пост в канал', 'опубликуй в телеграм', 'запости в мой канал'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Текст поста для Telegram-канала. Поддерживает Markdown. Пиши качественный контент по теме канала."
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
            "description": "📢 ПОСТ В DISCORD КАНАЛ: Публикует пост в Discord канал пользователя через webhook. Требует: webhook должен быть настроен в профиле (Настройки → Discord webhook). Если не настроен — объясни как добавить. Ключевые слова: 'пост в дискорд', 'опубликуй в discord', 'запости в мой discord канал'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Текст поста для Discord канала. Пиши качественный контент по теме."
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
    {
        "type": "function",
        "function": {
            "name": "get_message_status",
            "description": "📊 Показать статус отправленных сообщений — кто прочитал, кто ответил. Вызывай когда пользователь спрашивает 'ответил ли?', 'что с сообщением?', 'статус переписки' или когда в контексте есть НОВЫЕ ОТВЕТЫ.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    # ═════ EMAIL OUTREACH — автономное привлечение клиентов ═════
    {
        "type": "function",
        "function": {
            "name": "start_email_campaign",
            "description": "📧 ЗАПУСТИТЬ EMAIL-КАМПАНИЮ для привлечения клиентов. AI-агент автономно найдёт email-адреса через веб-поиск, напишет персонализированные письма, отправит через Resend и ответит на входящие в рамках цели. Используй когда: 'найди клиентов по email', 'запусти email-рассылку', 'привлечь клиентов через email', 'напиши предложение компаниям'. СНАЧАЛА спроси: что предлагаем, кому, какая цель.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Название кампании. Пример: 'Привлечение клиентов для AI-сервиса'"
                    },
                    "goal": {
                        "type": "string",
                        "description": "Подробная цель кампании — что именно хотим добиться. Пример: 'Найти 10 компаний, заинтересованных в AI-автоматизации, назначить демо-встречи'"
                    },
                    "target_audience": {
                        "type": "string",
                        "description": "Описание целевой аудитории. Пример: 'IT-директора компаний 50-500 человек в РФ, занимающихся e-commerce'"
                    },
                    "offer": {
                        "type": "string",
                        "description": "Что предлагаем — продукт, услуга, ценностное предложение. Пример: 'AI-ассистент для автоматизации поддержки клиентов, снижает нагрузку на 40%'"
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
                        "description": "Макс. количество писем в кампании (по умолчанию 50)",
                        "default": 50
                    },
                    "daily_limit": {
                        "type": "integer",
                        "description": "Макс. писем в день (по умолчанию 10)",
                        "default": 10
                    }
                },
                "required": ["name", "goal", "target_audience", "offer"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_outreach_email",
            "description": "📤 ОТПРАВИТЬ EMAIL в рамках кампании. Используй после web_search когда нашёл контактный email потенциального клиента. AI пишет персонализированное письмо с учётом цели кампании и контекста получателя. Также вызывается автономно якорем email_outreach_send.",
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
                        "description": "Компания получателя"
                    },
                    "recipient_context": {
                        "type": "string",
                        "description": "Почему этот контакт релевантен кампании. Пример: 'CTO стартапа в e-commerce, активно говорит про AI-автоматизацию в LinkedIn'"
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
            "description": "📋 ДОБАВИТЬ EMAIL-АДРЕСА в кампанию. Используй после web_search, когда нашёл несколько email-адресов потенциальных клиентов. Принимает JSON-массив или список email через запятую.",
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
]
