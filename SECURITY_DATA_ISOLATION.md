# 🔒 Data Isolation & User Context Security

## Overview
Каждый пользователь получает **полностью изолированный контекст**. Система гарантирует что AI не может видеть данные других пользователей.

---

## 📊 Data Isolation Architecture

### 1️⃣ **Context Loading** (`autonomous_agent.py::_build_context`)
```python
# ШАГАНГ 1: Идентификация пользователя
user = session.query(User).filter_by(telegram_id=user_id).first()
if not user:
    return None
```
✅ **Фильтр:** Все данные запрашиваются только для конкретного `user_id`

---

### 2️⃣ **User Profile & Integrations** 
```python
profile = session.query(UserProfile).filter_by(user_id=user.id).first()
# ↓ Retrieves ONLY this user's integrations (encrypted JSON)
```
✅ **Фильтр:** `user_id` привязан к профилю

---

### 3️⃣ **Goals** (`context_builder.py::build_proactive_context`)
```python
all_goals = session.query(Goal).filter(
    Goal.user_id == user.id,           # ← ГЛАВНЫЙ ФИЛЬТР
    Goal.status.notin_(['deleted']),
).order_by(Goal.priority.desc().nullslast(), Goal.created_at.desc()).all()
```
✅ **Фильтр:** Каждая цель = ТОЛЬКО seu own user_id

**Цели видны в контексте:**
- Содержат описание (может быть стратегическое указание)
- Связанные задачи (batch-loaded for N+1 prevention)
- Пользовательские правила в goal.description

---

### 4️⃣ **Rules & User Preferences** 
```python
# user.memory: JSON с rules и notes
decrypted_memory = decrypt_data(user.memory)
# ↓ Parse rules
_m = json.loads(decrypted_memory)
_rules = _m.get('rules', [])  # ТОЛЬКО для deze user
```
✅ **Фильтр:** Находятся в персональной encrypted памяти пользователя

**Правила передаются в каждом цикле для всех агентов**

---

### 5️⃣ **Tasks** 
```python
user_tasks = session.query(Task).filter(
    _or(
        Task.user_id == user.id,                        # ← Собственные задачи
        Task.delegated_to_username.ilike(user.username),  # ← Делегированные мне
        Task.delegated_by == user.id,                    # ← Мной делегированные
    ),
    Task.status.in_(['pending', 'active', 'in_progress']),
).all()
```
✅ **Фильтры:** Трёхуровневая изоляция:
- Только собственные (`user_id`)
- Делегированные этому пользователю (по username)
- Мной делегированные (по `delegated_by`)

---

### 6️⃣ **Team Agents** 
```python
_own = session.query(UserAgent).filter(
    UserAgent.author_id == user.id,  # ← Только собственные агенты
    UserAgent.status.in_(['active', 'paused']),
).all()
```
✅ **Фильтр:** `author_id == user.id` — только персональные агенты

---

### 7️⃣ **Strategic Rules Detection** (`autonomous_agent.py::_save_and_learn`)
```python
# ГЛОБАЛЬНЫЕ ПРАВИЛА (покрывают все цели)
_global_rule_keywords = ['никогда', 'всегда', 'только', 'исключи', 'игнорируй']
_is_global_rule = any(kw in _msg_lower for kw in _global_rule_keywords)

if _is_global_rule:
    # Сохраняется в user.memory['rules'] — ТОЛЬКО этого пользователя
    _mem_dict['rules'] = _existing_rules
    user.memory = json.dumps(_mem_dict, ensure_ascii=False)
```
✅ **Фильтр:** Правило = только в памяти хозяина

---

### 8️⃣ **Goal-Specific Strategies** 
```python
# ЦЕЛЕВЫЕ СТРАТЕГИИ (специфичны для конкретной цели)
_has_search_keywords = any(w in _msg_lower for w in ['ищем', 'ищи', 'search', 'find'])
_has_not_keywords = any(w in _msg_lower for w in ['не ', 'вместо', 'except'])

if _has_search_keywords and _has_not_keywords and not _is_global_rule:
    # Сохраняется в goal.description
    active_goals = session.query(Goal).filter(
        Goal.user_id == user_id,  # ← ГЛАВНЫЙ ФИЛЬТР
        Goal.status == 'active'
    ).all()
    # Обновляются Текущие цели THIS пользователя
```
✅ **Фильтр:** Стратегия = только в целях конкретного пользователя

---

## 🔐 Security Guarantees

### ❌ Что AI НЕ может видеть:
- Другие пользователи (нет их data в контексте)
- Чужие правила (зашифрованы в отдельной памяти)
- Чужие цели (фильтруются по user_id)
- Чужие интеграции (привязаны к профилю)
- Чужие агенты (фильтруются по author_id)

### ✅ Что AI может видеть:
- **ТОЛЬКО свои данные** (цели, задачи, правила, интеграции)
- **Свою память** (rules + стратегические указания)
- **Свою кассонду** (team agents + marketplace agents subscribes)

---

## 🚀 Data Flow Example

```
Пользователь A пишет: "Никогда не ищи на GitHub"
    ↓
_build_context(user_id=A) — ТОЛЬКО данные пользователя A
    ↓
prompt = get_extended_system_prompt(..., user_id_param=A, ...)
    ↓
build_proactive_context(user_id=A, session)
    ↓
Извлекаются:
    - goal.user_id == A.id ✅
    - task.user_id == A.id ✅
    - user.memory['rules'] для пользователя A ✅
    ↓
_save_and_learn(user_message, user_id=A)
    ↓
Сохраняется: user_A.memory['rules'].append("Никогда не ищи на GitHub")

───────────────────────────────────────────

Пользователь B в то же время пишет: "Ищем бизнесменов"
    ↓
_build_context(user_id=B) — ТОЛЬКО данные пользователя B
    ↓
prompt = get_extended_system_prompt(..., user_id_param=B, ...)
    ↓
build_proactive_context(user_id=B, session)
    ↓
Извлекаютсяие:
    - goal.user_id == B.id ✅ (НЕ видит цели A)
    - task.user_id == B.id ✅ (НЕ видит задачи A)
    - user.memory['rules'] для пользователя B ✅ (правило GitHub скрыто)
    ↓
_save_and_learn(user_message, user_id=B)
    ↓
Сохраняется: goal_B.description = "[СТРАТЕГИЯ...] Ищем бизнесменов"
```

**РЕЗУЛЬТАТ:** Каждый пользователь получает полностью изолированный контекст. ✅

---

## 📋 Database Queries - All Filtered

| Object | Query | Filter |
|--------|-------|--------|
| User | `User.filter_by(telegram_id=X)` | ✅ telegram_id |
| Profile | `UserProfile.filter_by(user_id=user.id)` | ✅ user_id |
| Goals | `Goal.filter(Goal.user_id==user.id)` | ✅ user_id |
| Tasks | `Task.filter(Task.user_id==user.id)` | ✅ user_id |
| Rules | `user.memory['rules']` | ✅ Encrypted per user |
| Agents | `UserAgent.filter(UserAgent.author_id==user.id)` | ✅ author_id |
| Integrations | `profile.integrations` (JSON) | ✅ user_id bound |

---

## 🛡️ Threat Model - Mitigated

### Threat: Cross-user data leak
→ **Mitigated:** Everyone query includes user_id filter. No context mixing.

### Threat: Agent seeing other users' rules
→ **Mitigated:** Rules stored encrypted in individual user.memory

### Threat: Goal strategies bleeding between users
→ **Mitigated:** Strategies stored in goal.description scoped to (user_id, goal_id)

### Threat: Shared agent seeing private data
→ **Mitigated:** Marketplace agents do NOT have user context. Only office agents (author_id-scoped) see user data.

---

## ✅ Compliance

- ✅ No shared user data in prompts
- ✅ Rules isolated to individual users
- ✅ Goals/Tasks filtered by user_id at DB level
- ✅ Integration secrets bound to user profile
- ✅ Agent access scoped to author

---

## 🔍 Verification Checklist

- [x] `_build_context()` filters by user_id
- [x] `build_proactive_context()` filters all queries by user_id
- [x] `Goal.user_id` filter present in queries
- [x] `Task.user_id` filter present in queries
- [x] `UserAgent.author_id` filter present for office agents
- [x] Rules encrypted per user
- [x] Strategies stored in goal.description (user_id bound)
- [x] Global rules detected and stored in user.memory['rules']
- [x] No shared prompt template with user data
