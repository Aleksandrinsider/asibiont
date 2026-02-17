# Enhanced TOOLS with clear, specific descriptions to prevent AI hallucinations

# Функции для ПОЛУЧЕНИЯ делегированных задач - доступны ВСЕМ (включая LIGHT)
DELEGATION_RECEIVE_FUNCTIONS = {
    'accept_delegated_task',
    'reject_delegated_task'
}

# Функции для ОТПРАВКИ делегирования - доступны только STANDARD и PREMIUM
DELEGATION_SEND_FUNCTIONS = {
    'delegate_task',
    'get_delegation_progress',
}

# Все функции делегирования
DELEGATION_FUNCTIONS = DELEGATION_RECEIVE_FUNCTIONS | DELEGATION_SEND_FUNCTIONS

# Фоновые алерты — доступны ВСЕМ тарифам (автоматические уведомления о контактах)
ALERT_FUNCTIONS = {
    'set_contact_alert',
}

# Инструменты исключённые из обнаружения (дубли, устаревшие)
# Функции-обработчики остаются в handlers.py, но не предлагаются AI
EXCLUDED_TOOLS = {
    'web_search',                       # дубль research_topic
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
}

# Автопилот канала — только PREMIUM (автономное ведение)
PREMIUM_AUTOPILOT_FUNCTIONS = {
    'set_auto_post_time',
    'set_content_strategy',
}

# Ограниченные функции для каждого тарифа (что БЛОКИРУЕТСЯ)
# LIGHT: не может делегировать, нет автопилота (но может получать делегированные + алерты)
LIGHT_RESTRICTED = DELEGATION_SEND_FUNCTIONS | PREMIUM_AUTOPILOT_FUNCTIONS

# STANDARD: может делегировать + алерты, но нет автопилота канала
STANDARD_RESTRICTED = PREMIUM_AUTOPILOT_FUNCTIONS

# PREMIUM: нет ограничений
PREMIUM_RESTRICTED = set()

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
    
    # Check if dynamic tools are discovered
    if tool_discovery.discovered_tools:
        # Use dynamic tool filtering
        return tool_discovery.get_available_tools_for_tier(tier_value)
    else:
        # Fallback to static TOOLS filtering
        tier_value = tier_value.upper()
        
        # Determine restricted functions based on tier
        if tier_value == 'PREMIUM':
            restricted = PREMIUM_RESTRICTED
        elif tier_value == 'STANDARD':
            restricted = STANDARD_RESTRICTED
        else:  # LIGHT
            restricted = LIGHT_RESTRICTED
        
        # Filter out restricted functions and excluded tools
        return [tool for tool in TOOLS 
               if tool['function']['name'] not in restricted
               and tool['function']['name'] not in EXCLUDED_TOOLS]

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
            "description": "Создать НОВУЮ задачу. Для переноса существующих — edit_task(reminder_time=...).",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Краткое название задачи (2-5 слов). Извлекай суть из запроса пользователя.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Доп. детали, НЕ повторяющие название. Оставляй пустым если деталей нет.",
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
                },
                "required": ["title", "reminder_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Отметить задачу выполненной. Ключевые слова: 'сделал', 'готово', 'завершил', 'закрыть'. task_title опционален если есть задача в фокусе.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_title": {
                        "type": "string",
                        "description": "Ключевые слова задачи (опционально). При 'готово'/'сделал' — оставь пустым.",
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
            "name": "edit_task",
            "description": "ИЗМЕНИТЬ СУЩЕСТВУЮЩУЮ ЗАДАЧУ (название, описание, время). Вызывай когда пользователь хочет изменить текст или время задачи. Ключевые слова: 'измени', 'исправь', 'обнови', 'перенеси', 'переназначь'. Примеры: 'измени название задачи на X' → edit_task, 'перенеси задачу на завтра' → edit_task(reminder_time='завтра в 10:00'), 'добавь описание' → edit_task",
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
            "description": "Удалить задачу НАВСЕГДА. Ключевые слова: 'удали', 'убери', 'сотри', 'удалить'. ВАЖНО: если пользователь говорит 'удали задачу' — ВСЕГДА вызывай delete_task, даже если он упоминает что задача уже сделана. НЕ путай с complete_task.",
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
            "description": "Обновить профиль. ДОБАВЛЯЕТ в skills/interests (не заменяет). Вызывай при явном упоминании личных данных. ЗАПРЕЩЕНО выдумывать данные.",
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
            "name": "show_profile",
            "description": "Показать профиль пользователя. Ключевые слова: 'покажи профиль', 'мой профиль'. Для обновления — update_profile.",
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
                "properties": {}
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
            "name": "set_auto_post_time",
            "description": "Время автопостинга (PREMIUM). AI постит ежедневно в указанное время.",
            "parameters": {
                "type": "object",
                "properties": {
                    "post_time": {
                        "type": "string",
                        "description": "Время постинга в формате HH:MM (24-часовой формат). Примеры: '14:30', '09:15', '18:00'"
                    }
                },
                "required": ["post_time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_marketing_content",
            "description": "Генерация маркетингового контента: заголовок, пост, хэштеги, CTA. Генерируй сразу, не спрашивай детали.",
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
            "description": "🔍 Глубокое исследование через Google Search + AI-анализ. Вызывай для содержательных вопросов, где нужны актуальные данные и экспертиза: рынок, технологии, конкуренты, стратегии, тренды, бизнес, финансы, здоровье. Не нужен для прямых команд (создай задачу), подтверждений (ок, давай) или данных уже имеющихся в контексте.",
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
            "name": "get_weather_info",
            "description": "🌤️ Погода для города. Вызывай при вопросах о погоде.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "Название города на русском или английском. Примеры: 'Москва', 'Moscow', 'Санкт-Петербург', 'Yekaterinburg'"
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
            "name": "publish_to_telegram",
            "description": "📢 ПУБЛИКАЦИЯ В TELEGRAM (ВСЕ ТАРИФЫ): Публикует пост в Telegram канал пользователя. Требуется настроенный telegram_channel в профиле. Используй ПОСЛЕ generate_marketing_content или когда пользователь просит 'опубликуй пост', 'запости это'. Бот должен быть админом канала.",
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
            "name": "create_goal",
            "description": "🎯 СОЗДАТЬ ЦЕЛЬ: Долгосрочная цель пользователя с отслеживанием прогресса. Цели объединяют задачи и показывают общий прогресс. Ключевые слова: 'хочу достичь', 'моя цель', 'планирую за N месяцев', 'стремлюсь к'. Примеры: 'хочу выучить Python за 3 месяца' → create_goal(title='Выучить Python', category='learning', target_date='через 3 месяца'). ПОСЛЕ создания цели предложи разбить её на задачи через add_task!",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Краткое название цели (2-8 слов). Примеры: 'Выучить Python', 'Запустить стартап', 'Пробежать марафон'"
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
                        "description": "Как измерить успех. Примеры: 'сдать сертификацию', 'пробежать 42 км за 4 часа', 'выручка 1 млн'"
                    }
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_goal_progress",
            "description": "📊 ОБНОВИТЬ ПРОГРЕСС ЦЕЛИ: Изменить процент, статус или добавить заметку к цели. Ключевые слова: 'прогресс по цели', 'я продвинулся', 'обнови цель', 'завершил цель', 'приостанови цель'. Примеры: 'прогресс по Python 40%' → update_goal_progress(goal_title='Python', progress=40), 'завершил цель марафон' → update_goal_progress(goal_title='марафон', status='completed')",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal_title": {
                        "type": "string",
                        "description": "Название или ключевые слова цели для поиска"
                    },
                    "progress": {
                        "type": "integer",
                        "description": "Процент выполнения (0-100). При 100% цель автоматически завершается"
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
            "name": "set_content_strategy",
            "description": "🎯 НАСТРОИТЬ СТРАТЕГИЮ КОНТЕНТА (ТОЛЬКО ПРЕМИУМ): Сохраняет стратегию контента для автономного ведения канала. AI будет автоматически генерировать и публиковать контент по этой стратегии. Примеры: 'хочу постить про свой бизнес', 'буду публиковать кейсы по дизайну'. ВАЖНО: Спроси детали — что постить, для кого, цель. [ТОЛЬКО ПРЕМИУМ]",
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
]
