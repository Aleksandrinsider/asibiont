# 🚀 План монетизации AI-агентов

## 📋 Текущая инфраструктура (уже работает)

| Механизм | Файлы | Доход |
|----------|-------|-------|
| Роялти агентов (70% от сообщений) | [`models.py:930-932`](../models.py:930), [`user_agents.py:483-585`](../ai_integration/user_agents.py:483) | Токены автору |
| Рефералы: +500 токенов + 20% комиссии | [`handlers.py:571-587`](../handlers.py:571), [`main.py:4079-4132`](../main.py:4079) | Токены |
| AI Arena — экспозиция агентов | [`agent_arena.py:290-338`](../ai_integration/agent_arena.py:290) | Косвенный |
| Пакеты токенов (1500₽/5000₽/50000₽) | [`token_service.py:191-195`](../token_service.py:191), [`payments.py:23-27`](../payments.py:23) | Рубли/крипто |
| Crypto-платежи (USDT) | [`crypto_payments.py:26-30`](../crypto_payments.py:26) | USD |

---

## 🟢 Фаза 1: Быстрый доход (реализовать в первую очередь)

### 1.8 Marketplace Script Monetization — монетизация скриптов

**Суть:** Сейчас `install_script` стоит 3 токена, но автор не получает ничего. Нужно внедрить роялти за скрипты (как у агентов).

**Файлы для изменений:**
- [`models.py:892-974`](../models.py:892) — добавить `script_price`, `script_royalty_pct` в `UserAgent`
- [`ai_integration/tools.py`](../ai_integration/tools.py) — обновить `install_script`, `run_user_script` биллинг
- [`token_service.py`](../token_service.py) — добавить тарифы `install_script_paid`, `run_script_paid`
- [`main.py:12573-13511`](../main.py:12573) — API publish/update с script_price

**TODO:**
1. Добавить поля `script_price`, `script_royalty_pct` в модель UserAgent
2. Миграция БД
3. Обновить `bill_agent_message()` или создать `bill_script_run()` в `user_agents.py`
4. Обновить API публикации скрипта (price_per_message → включая script_price)
5. Обновить `run_user_script` — списывать токены, начислять автору

---

### 1.1 Affiliate-маркетинг (партнёрские программы)

**Суть:** AI-агент автоматически находит партнёрские программы по теме, создаёт контент с реф-ссылками и публикует в Telegram/Discord каналы владельца.

**Файлы для изменений:**
- [`ai_integration/tools.py`](../ai_integration/tools.py) — новый инструмент `find_affiliate_programs`, `generate_affiliate_post`
- [`token_service.py`](../token_service.py) — тарифы `find_affiliate_programs`, `generate_affiliate_post`
- [`ai_integration/marketing_agent.py`](../ai_integration/marketing_agent.py) — интеграция с генерацией контента
- [`anchor_engine.py`](../anchor_engine.py) — новый anchor_type `affiliate_post_opportunity`

**TODO:**
1. Создать базу партнёрских программ (можно в конфиге или embedded JSON)
2. AI-инструмент `find_affiliate_programs(topic)` — поиск релевантных партнёрок через web_search
3. AI-инструмент `generate_affiliate_post(program, platform)` — создание поста с реф-ссылкой
4. Интеграция с `publish_to_telegram` / `publish_to_discord`
5. Anchor-сканирование: если у пользователя есть Telegram-канал и тематика → создать anchor для affiliate-поста
6. Dashboard-статистика: клики, конверсии, заработок

---

### 1.3 Content Paywall — монетизация контента

**Суть:** Агент создаёт ценный контент (аналитика, отчёты, гайды) и продаёт доступ за токены через paywall.

**Файлы для изменений:**
- [`models.py:340-354`](../models.py:340) — добавить `price_tokens` в `Post`, тип `paid`
- [`main.py`](../main.py) — API `POST /api/posts/{id}/unlock`
- [`ai_integration/tools.py`](../ai_integration/tools.py) — новый инструмент `create_paid_post`
- [`token_service.py`](../token_service.py) — тариф `unlock_post`

**TODO:**
1. Добавить `is_paid`, `price_tokens`, `unlocked_by` в модель Post
2. API `POST /api/posts/{id}/unlock` — списание токенов, возврат контента
3. AI-инструмент `create_paid_post(title, content, price)` — создание платного поста
4. Anchor-сканирование: если у пользователя есть ниша → предложить создать платный гайд/отчёт
5. Статистика: сколько раз куплен, сколько заработано

---

## 🟡 Фаза 2: Экосистема

### 2.4 Lead Generation as a Service — продажа лидов

**Суть:** Агент находит платёжеспособных клиентов/партнёров и продаёт лидов владельцу или другим пользователям за токены.

**Файлы для изменений:**
- [`models.py`](../models.py) — новая модель `LeadListing(price, lead_data, buyer_id, status)`
- [`ai_integration/tools.py`](../ai_integration/tools.py) — `find_buyers`, `list_leads_for_sale`, `buy_lead`
- [`main.py`](../main.py) — API лидов
- [`token_service.py`](../token_service.py) — тарифы

**TODO:**
1. Модель `LeadListing` (автор, данные лида, цена, статус: available/sold)
2. Инструмент `find_buyers(product_description, budget_min)` — поиск через find_partners + скоринг
3. Инструмент `list_leads_for_sale(category)` — витрина лидов
4. Инструмент `buy_lead(lead_id)` — списание токенов, передача данных
5. Инструмент `rate_lead_quality(lead_id, quality_score)` — рейтинг качества
6. Dashboard: сколько лидов продано, заработок, конверсия

---

### 2.5 Agent-to-Agent Commerce — B2B между агентами

**Суть:** Агенты заказывают услуги друг у друга с оплатой токенами через escrow.

**Файлы для изменений:**
- [`models.py`](../models.py) — новая модель `AgentB2BOrder(id, buyer_agent_id, seller_agent_id, budget, status)`
- [`ai_integration/tools.py`](../ai_integration/tools.py) — `create_b2b_order`, `fulfill_b2b_order`
- [`ai_integration/autonomous_agent.py`](../ai_integration/autonomous_agent.py) — новый синтаксис `DELEGATE[Агент: задача, бюджет]`
- [`main.py`](../main.py) — API B2B
- [`token_service.py`](../token_service.py) — тарифы

**TODO:**
1. Модель `AgentB2BOrder` (buyer_agent_id, seller_agent_id, task_description, budget_tokens, status, escrow_held)
2. Расширить синтаксис DELEGATE: `DELEGATE[ИмяАгента: задача, бюджет: 50]`
3. Escrow: при создании заказа бюджет замораживается на балансе buyer-агента
4. При выполнении: токены переходят seller-агенту, платформа берёт комиссию (10-15%)
5. При отмене: токены возвращаются buyer-агенту (минус штраф 5%)
6. Витрина B2B-услуг агентов: агенты публикуют что могут делать

---

## 🟠 Фаза 3: Масштабирование

### 3.2 Service Marketplace — продажа услуг через агентов

**Суть:** Агент продаёт услуги владельца (консультации, дизайн, копирайтинг). Клиенты платят токенами. Escrow.

**Файлы для изменений:**
- [`models.py:892-974`](../models.py:892) — добавить `service_catalog` (JSON)
- [`ai_integration/tools.py`](../ai_integration/tools.py) — `offer_service`, `order_service`, `complete_service`
- [`main.py`](../main.py) — API сервисов
- [`token_service.py`](../token_service.py) — тарифы
- [`anchor_engine.py`](../anchor_engine.py) — anchor `service_upsell`

**TODO:**
1. Добавить `service_catalog` в `UserAgent` (JSON: [{name, description, price_tokens, delivery_time}])
2. Инструмент `offer_service(user_id, service_name)` — агент предлагает услугу
3. Инструмент `order_service(agent_id, service_name)` — заказ с escrow
4. Инструмент `complete_service(order_id)` — подтверждение выполнения, разморозка токенов
5. Anchor: если агент видит запрос пользователя, совпадающий с его услугами → upsell
6. Dashboard: заказы, выполнено, заработок, отзывы

---

### 3.6 Token Staking — стейкинг токенов

**Суть:** Заморозка токенов → пассивный доход (APY 5-20% в зависимости от срока).

**Файлы для изменений:**
- [`models.py`](../models.py) — новая модель `TokenStake`
- [`token_service.py`](../token_service.py) — `stake_tokens()`, `unstake_tokens()`, `calculate_staking_rewards()`
- [`main.py`](../main.py) — API `/api/staking/*`
- [`handlers.py`](../handlers.py) — Telegram-команда `/stake`

**TODO:**
1. Модель `TokenStake` (user_id, amount, lock_months, apy, start_date, end_date, status)
2. APY: 5% на 3 мес, 10% на 6 мес, 15% на 12 мес
3. Механизм начисления процентов (ежедневно/ежемесячно)
4. Unstake: досрочное снятие со штрафом 20% (токены сгорают — дефляция)
5. Крон/job: `calculate_staking_rewards()` — раз в день начисляет проценты
6. Dashboard: стейкинг, доход, APY

---

### 3.7 Premium Agent Features — платные апгрейды

**Суть:** Базовое создание агентов бесплатно. Расширенные возможности — за токены.

**Файлы для изменений:**
- [`models.py:892-974`](../models.py:892) — добавить `premium_features` (JSON)
- [`ai_integration/user_agents.py`](../ai_integration/user_agents.py) — проверка доступа при создании/редактировании
- [`main.py`](../main.py) — API покупки фич
- [`token_service.py`](../token_service.py) — тарифы premium-фич

**TODO:**
1. Пакет фич (цена в токенах):
   - `advanced_prompting: 500` — доступ к расширенному system prompt
   - `priority_processing: 1000` — приоритетная очередь AI-вызовов
   - `premium_models: 2000` — доступ к GPT-4/Claude (сверх DeepSeek)
   - `white_label: 5000` — убрать брендинг платформы
   - `analytics_dashboard: 1500` — расширенная аналитика агента
   - `custom_tools: 3000` — подключение внешних API
2. Проверка `has_premium_feature(agent_id, feature)` при создании/редактировании
3. API покупки/отмены фичи
4. Dashboard: какие фичи активны, управление подпиской

---

## 📊 Roadmap

```
Фаза 1 (сейчас)
├── 1.8 Скрипты с роялти        [1-2 дня]
├── 1.1 Affiliate-маркетинг     [2-3 дня]
└── 1.3 Content Paywall         [2-3 дня]

Фаза 2 (после Фазы 1)
├── 2.4 Продажа лидов           [3-4 дня]
└── 2.5 B2B агентов             [4-5 дней]

Фаза 3 (после Фазы 2)
├── 3.2 Service Marketplace     [3-4 дня]
├── 3.6 Token Staking           [2-3 дня]
└── 3.7 Premium Features        [2-3 дня]
```

---

## 🔧 Общая инфраструктура (нужно для всех фаз)

1. **Журнал TokenTransaction** ([`models.py`](../models.py)) — расширить `action` типами: `affiliate_earning`, `lead_sale`, `b2b_payment`, `service_payment`, `staking_reward`, `premium_feature`, `script_royalty`
2. **Dashboard-статистика** ([`templates/dashboard_new.html`](../templates/dashboard_new.html)) — блок "Заработок": график дохода, баланс, история транзакций
3. **Telegram-команды** ([`handlers.py`](../handlers.py)) — `/earnings` — показать заработок, `/stake` — стейкинг

---

## 📐 Архитектура

```mermaid
flowchart TD
    subgraph "Фаза 1: Быстрый доход"
        A[1.8 Script Royalty] --> A1[script_price/royalty_pct в UserAgent]
        A1 --> A2[bill_script_run в user_agents.py]
        
        B[1.1 Affiliate] --> B1[find_affiliate_programs tool]
        B1 --> B2[generate_affiliate_post tool]
        B2 --> B3[Автопостинг с реф-ссылками]
        
        C[1.3 Paywall] --> C1[is_paid/price_tokens в Post]
        C1 --> C2[/api/posts/id/unlock]
    end
    
    subgraph "Фаза 2: Экосистема"
        D[2.4 Lead Gen] --> D1[find_buyers tool]
        D1 --> D2[sell_lead tool]
        D2 --> D3[LeadListing model]
        
        E[2.5 B2B Agents] --> E1[AgentB2BOrder model]
        E1 --> E2[Escrow биллинг]
        E2 --> E3[DELEGATE с бюджетом]
    end
    
    subgraph "Фаза 3: Масштабирование"
        F[3.2 Services] --> F1[service_catalog в Agent]
        F1 --> F2[offer_service tool]
        F2 --> F3[order_service с escrow]
        
        G[3.6 Staking] --> G1[TokenStake model]
        G1 --> G2[APY расчёты]
        G2 --> G3[daily reward job]
        
        H[3.7 Premium] --> H1[premium_features JSON]
        H1 --> H2[has_premium_feature check]
    end
    
    subgraph "Общая инфраструктура"
        I[TokenTransaction - новые action типы]
        J[Dashboard - блок Заработок]
        K[Telegram - команды /earnings /stake]
    end
    
    A2 --> I
    B3 --> I
    C2 --> I
    D3 --> I
    E2 --> I
    F3 --> I
    G2 --> I
    H2 --> I
```
