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

# Фоновые алерты — STANDARD и PREMIUM (автоматические уведомления о контактах)
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
# LIGHT: не может делегировать, нет алертов и автопилота (но может получать делегированные)
LIGHT_RESTRICTED = DELEGATION_SEND_FUNCTIONS | ALERT_FUNCTIONS | PREMIUM_AUTOPILOT_FUNCTIONS

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
        
        # Filter out restricted functions
        return [tool for tool in TOOLS 
               if tool['function']['name'] not in restricted]

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
            "description": "⚠️ ТОЛЬКО ДЛЯ НОВЫХ ЗАДАЧ! Создает новую задачу с напоминанием. После создания задачи про активность - ОБЯЗАТЕЛЬНО вызови find_relevant_contacts_for_task чтобы предложить подходящих людей. Для переноса существующих задач используй edit_task(reminder_time=...).",
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
            "description": "⚠️ МОНИТОРИНГ КОНТАКТОВ (СТАНДАРТ+): Настроить автоматическое уведомление о новых пользователях с нужными навыками/интересами. Ключевые слова: 'мониторь @user', 'отслеживай контакт', 'скажи когда появится Python разработчик'. Система мониторит регистрации и обновления профилей, и АВТОМАТИЧЕСКИ добавит информацию в следующий диалог. Примеры: 'скажи когда появится специалист по продажам' → set_contact_alert(skill='продажи'). [СТАНДАРТ или ПРЕМИУМ]",
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
            "description": "⏰ НАСТРОЙКА ВРЕМЕНИ АВТОПОСТИНГА (PREMIUM): Установить предпочтительное время для автоматической публикации контента. AI будет постить каждый день ровно в указанное время в вашем часовом поясе. Ключевые слова: 'постить в 14:00', 'автопостинг в 9 утра', 'каждый день в 18:30'. ЕСЛИ пользователь говорит о времени постинга - ОБЯЗАТЕЛЬНО ВЫЗОВИ set_auto_post_time()! Примеры: 'хочу постить каждый день в 14:00' → set_auto_post_time(post_time='14:00'), 'автопостинг в 9:15' → set_auto_post_time(post_time='09:15'). [ТОЛЬКО PREMIUM]",
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
            "description": "🚀 AI МАРКЕТИНГ (ВСЕ ТАРИФЫ): Автоматическая генерация профессионального маркетингового контента для привлечения клиентов. AI создаст цепляющий заголовок, текст поста, хэштеги и призыв к действию. ⚙️ НЕ СПРАШИВАЙ ДЕТАЛИ - ГЕНЕРИРУЙ СРАЗУ с тем что есть! Используй разумные defaults. Примеры: 'напиши пост про AI', 'создай рекламу для Instagram'.",
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
            "description": "🔍 УНИВЕРСАЛЬНЫЙ ПОИСК И АНАЛИЗ (ВСЕ ТАРИФЫ): AI-powered исследование через Google Search с глубоким анализом. Автоматически находит 10+ свежих источников, анализирует тренды и дает actionable insights. Доступно для всех тарифов с одинаковым качеством. Примеры: 'рынок AI-агентов', 'тренды в стартапах 2026', 'конкуренты в нише X'.",
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
            "description": "🌤️ ПОГОДА (ВСЕ ТАРИФЫ): Получить актуальную информацию о погоде для любого города. Показывает температуру, влажность, ветер и описание погоды. Ключевые слова: 'погода в Москве', 'какая погода', 'прогноз погоды'. ЕСЛИ пользователь спрашивает о погоде - ОБЯЗАТЕЛЬНО ВЫЗОВИ get_weather_info()! Примеры: 'какая погода в СПб?' → get_weather_info(city='Санкт-Петербург'), 'погода в Екатеринбурге' → get_weather_info(city='Екатеринбург').",
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
            "description": "📰 НОВОСТИ И ТРЕНДЫ (ВСЕ ТАРИФЫ): Анализ новостей и трендов по теме с помощью AI. Ищет свежие новости, анализирует тренды и дает инсайты. Ключевые слова: 'новости по AI', 'тренды в бизнесе', 'что происходит в отрасли'. ЕСЛИ пользователь спрашивает о новостях/трендах - ОБЯЗАТЕЛЬНО ВЫЗОВИ get_news_trends()! Примеры: 'новости по AI' → get_news_trends(topic='AI', period='week', focus='trends'), 'тренды в стартапах' → get_news_trends(topic='стартапы', period='month', focus='opportunities').",
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
