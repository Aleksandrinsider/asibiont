# Анализ интеграций AI-агентов

> **Примечание:** Все жёсткие дневные лимиты (кроме safety net для GitHub issues) УДАЛЕНЫ по запросу пользователя.
> Вместо них внедрены умные механизмы: улучшенный system prompt, quality-aware описания инструментов,
> fuzzy-dedup заголовков GitHub issues, и контекст сессии. AI должен сам принимать правильные решения.

## 1. Полный список интеграций (60+)

### 🔌 Прямые инструменты (через `tools.py` → `handlers.*`)

| Инструмент | Категория | Описание | Лимиты |
|-----------|-----------|----------|--------|
| `create_github_issue` | GitHub | Создание issue | ✅ **5/день** (safety net), ✅ **2 за turn**, 🔍 **smart dedup** заголовков |
| `close_github_issue` | GitHub | Закрытие issue | ✅ **5/день** (safety net), ✅ **2 за turn** |
| `analyze_github_code` | GitHub | Code review PR/репозитория | ❌ нет лимитов |
| `trigger_github_workflow` | GitHub | Запуск CI/CD | ❌ нет лимитов |
| `push_file_to_github` | GitHub | Коммит файла | ❌ нет лимитов |
| `create_post` | Контент | Публикация в блог | ✅ **3/день**, ✅ 1 за сессию |
| `publish_to_telegram` | Контент | Пост в Telegram | ✅ **3/день**, ✅ 1 за сессию |
| `publish_to_discord` | Контент | Пост в Discord | ✅ **3/день**, ✅ 1 за сессию |
| `publish_to_vk` | Соцсети | Пост ВКонтакте | ❌ нет лимитов |
| `publish_to_twitter` | Соцсети | Твит | ❌ нет лимитов |
| `publish_to_linkedin` | Соцсети | Пост в LinkedIn | ❌ нет лимитов |
| `publish_to_notion` | Notion | Страница в Notion | ❌ нет лимитов |
| `publish_to_youtube` | YouTube | Аналитика канала | ❌ нет лимитов |
| `send_outreach_email` | Email | Отправка письма | ✅ 12 за turn |
| `reply_to_outreach_email` | Email | Ответ на письмо | ✅ 12 за turn, 🔍 quality-check в описании |
| `send_follow_up_email` | Email | Follow-up письмо | ✅ 12 за turn, 🔍 quality-check в описании |
| `check_emails` | Email | Проверка входящих | ❌ нет лимитов |
| `send_email` | Email | Одиночное письмо | ✅ 12 за turn |
| `negotiate_by_email` | Email | Переговоры по email | ❌ нет лимитов |
| `start_email_campaign` | Email | Запуск кампании | ❌ нет лимитов |
| `web_search` | Поиск | Интернет-поиск | ✅ **5 за turn** |
| `research_topic` | Поиск | Глубокий анализ | ❌ нет лимитов |
| `run_agent_action` | Универсальное | Любое действие через скрипт агента | ✅ **16 за turn** |
| `http_api_request` | Универсальное | HTTP-запрос к любому API | ❌ без лимитов |
| `generate_image` | Изображения | Генерация картинки | ❌ нет лимитов |
| `generate_video` | Видео | Генерация видео | ❌ нет лимитов |
| `get_stock_price` | Финансы | Котировки | ❌ нет лимитов |
| `get_forex_analysis` | Финансы | Анализ форекс | ❌ нет лимитов |
| `get_news_trends` | Новости | Новости и тренды | ❌ нет лимитов |
| `initiate_phone_call` | Звонки | Twilio-звонок | ❌ нет лимитов |
| `delegate_task` | Делегирование | Поручение агенту | ✅ 12 за turn |
| `create_goal` | Цели | Создание цели | ✅ 8 за turn |
| `add_task` | Задачи | Создание задачи | ✅ 12 за turn |

### 🔧 Интеграции через `run_agent_action` (скрипт агента)

Эти интеграции не имеют собственных лимитов — работают через единый лимит `run_agent_action`: **16 за turn**.

| Категория | API/Сервисы | Действия |
|-----------|-------------|----------|
| **CRM** | AmoCRM, HubSpot, Bitrix24 | create_lead, update_lead, get_pipelines, search_contacts |
| **Маркетплейс** | Ozon, WB, Shopify, Avito, MoySklad | get_orders, get_products, update_stock, get_reviews |
| **Трекер задач** | Jira, Trello, ClickUp, Linear | create_issue, update_issue, get_sprint, move_card |
| **Notion** | Notion API | create_page, update_page, query_db |
| **Google Sheets** | Google Sheets API | append_row, update_cell, get_range |
| **Крипто** | Binance, Bybit, CoinGecko | get_price, get_balance, coingecko_price |
| **Финансы** | Alpha Vantage, Finnhub | get_price, get_quote, get_volume, get_rates |
| **Новости** | NewsAPI | get_news |
| **Slack** | Slack API | send_message |
| **Соцсети** | VK, Twitter API | post_wall, post_tweet |
| **Платежи** | Stripe, YooKassa | create_payment_link |
| **Календарь** | Google Calendar, Calendly | create_event, get_events |
| **Звонки/SMS** | Twilio, WhatsApp | send_sms, send_message, make_call |
| **Облако** | S3, GCS, Dropbox | upload, download, list_files |
| **Аналитика** | GA4, Metrika | get_metrics, get_report |
| **MS Teams** | Microsoft Graph | send_message |
| **Автоматизация** | Zapier, Make, n8n | trigger_webhook |
| **БД** | Supabase, Airtable, MongoDB | query, insert |
| **HR** | hh.ru, SuperJob | search_vacancies, get_resumes |
| **Реклама** | Яндекс.Директ | get_campaigns, update_campaign, get_stats |
| **Скрейпинг** | Любые URL | scrape_page |
| **AI API** | OpenAI, Gemini, Replicate | generate_text, replicate_run |
| **Изображения** | Replicate, Stable Diffusion | generate_image |
| **MarineTraffic** | MarineTraffic API | track_vessel, port_vessels |
| **Почта России** | Почта РФ API | track, calculate_tariff |

## 2. Как AI выбирает интеграции

### Процесс выбора инструмента:
1. **`_classify_agent_caps()`** — определяет какие категории интеграций подключены у агента (по API-ключам)
2. **Построение промпта** — в `anchor_engine.py` для каждого агента динамически генерируется блок инструментов:
   - Показываются ТОЛЬКО подключённые интеграции
   - Для каждой — конкретные action-имена
   - Указывается что НЕ подключено
3. **LLM выбирает** — AI (DeepSeek/OpenAI) решает какой инструмент вызвать на основе:
   - Описания инструмента в `tools.py`
   - Инструкций в system prompt
   - Контекста диалога и целей
4. **Валидация в `execute_actions()`**:
   - Проверка daily limit (publish)
   - Dedup (одинаковые вызовы)
   - Multi-limit per turn
   - Circuit breaker (слишком много ошибок)
   - User policy (правила пользователя)
   - Once-only (некоторые инструменты строго 1 раз)

### Автопилот (автономный режим):
- Если задача определена как `autopilot`, агент сам решает какие шаги делать
- На первой итерации без инструментов — система **принудительно ретраит** с указанием конкретного инструмента
- Приоритет: web_search → research_topic → run_agent_action → ... → **create_github_issue (теперь в конце)**

## 3. Найденные проблемы и узкие места

### 🔴 Проблема 1: create_github_issue (исправлено — умный подход)
- **Было**: без лимитов, высокий приоритет в автопилоте
- **Стало**: safety net 5/день + **smart dedup** (fuzzy-сравнение заголовков, >55% overlap = блокировка), понижен приоритет в автопилоте
- **Как это умнее лимита**: AI может создать 5 РАЗНЫХ issues, но не сможет создать 2 одинаковых. Качество > количество.

### 🟡 Проблема 2: Email-лимиты (исправлено)
- **Было**: daily_limit по умолчанию 1 000 000 писем
- **Стало**: 10 000

### 🟢 Проблема 3: Дневные лимиты — осознанное решение
- `reply_to_outreach_email`, `send_follow_up_email`, `http_api_request` — **без дневных лимитов**
- Вместо жёстких лимитов: улучшен system prompt (п.7 «КАЧЕСТВО ПЕРЕД КОЛИЧЕСТВОМ») + quality-aware описания инструментов
- AI сам решает когда и сколько — но с пониманием что каждый вызов должен быть осмысленным

### 🟢 Проблема 4: Дублирование инструментов
- `create_github_issue` (прямой вызов) и `run_agent_action(action='create_issue')` (через скрипт) — два разных способа
- Smart dedup работает ТОЛЬКО для прямого вызова `create_github_issue`
- `run_agent_action` может создать issue без проверки дубликатов

### 🟢 Проблема 5: Нет мониторинга успешности
- Инструменты не имеют механизма auto-disable при неудачах (кроме circuit breaker)
- Если агент раз за разом создаёт issues, которые никто не читает — он не учится на этом

## 4. Рекомендации (новый подход — умный AI вместо лимитов)

1. ✅ **Улучшен system prompt** — добавлен п.7 «КАЧЕСТВО ПЕРЕД КОЛИЧЕСТВОМ» в фреймворк рассуждений
2. ✅ **Quality-aware описания** — `create_github_issue`, `reply_to_outreach_email`, `send_follow_up_email` содержат ⚠️ КАЧЕСТВО: инструкцию о необходимости проверять дубликаты и ценность
3. ✅ **Smart dedup GitHub issues** — fuzzy-сравнение заголовков (word overlap >55%) блокирует дубликаты
4. ✅ **GitHub safety net** — 5/день как защита от крайних случаев (настраивается через `DAILY_GITHUB_ISSUE_LIMIT`)
5. 🔲 **Объединить `create_github_issue` и `run_agent_action(action='create_issue')`** — чтобы smart dedup работал для обоих путей
6. 🔲 **Добавить контекстную рефлексию** — AI должен видеть историю своих действий за сессию
