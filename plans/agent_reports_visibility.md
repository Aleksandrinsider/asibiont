# План: Видимость отчётов агентов + гендер

## Текущая ситуация

Агенты **реально запускаются** (Fix 5 работает — Lorenzo, Insider, Pablo, Beatrice, Hugo, Leo, Olivia успели отработать за цикл 10:01–10:19). Но:

1. **Отчёты агентов не сохраняются как видимые сообщения** — `_save_interaction_for_director` не вызывается после `_exec_agent_for_director` в L2-координаторе
2. **`gender` не передаётся в agent dict** — поэтому Olivia (женский род) получает мужские окончания («собрал», «сохранил»)
3. **`python_code`, `user_api_keys`, `tools_allowed` не передаются** — агент запускается как «голая» LLM без скрипта, интеграций и инструментов

---

## Root Cause 1: Отчёты не сохраняются

### Где
[`office_engine.py`](../ai_integration/office_engine.py:2006-2021) — `_process_one_assignment`

### Что
После `_exec_agent_for_director` возвращает `(final_text, tools_used, tokens)`, но результат:
- Не сохраняется через `_save_interaction_for_director` → не появляется в чате
- Не оборачивается в `__agent` JSON → нет аватара/имени агента
- Не отправляется через `_send_visible` (progress_callback) → нет SSE-события

### Исправление
Добавить после вызова `_exec_agent_for_director`:
```python
# ── Сохраняем отчёт агента как видимое сообщение ──
try:
    from ai_integration.autonomous_agent import _save_interaction_for_director
    _agent_report_text = _exec_result[0] if isinstance(_exec_result, (list, tuple)) else str(_exec_result or '')
    if _agent_report_text.strip():
        import json as _j_rep
        _agent_report_json = _j_rep.dumps({
            '__agent': {'name': _agent_name_db, 'id': _agent_id},
            'text': _agent_report_text.strip(),
        }, ensure_ascii=False)
        _save_interaction_for_director(user_db_id, _agent_report_json)
except Exception as _save_err:
    logger.warning("[OFFICE-L2] save report error: %s", _save_err)
```

---

## Root Cause 2: Gender не передаётся

### Где
1. [`office_engine.py`](../ai_integration/office_engine.py:1391) — сборка `agents` туплов
2. [`office_engine.py`](../ai_integration/office_engine.py:2000-2005) — сборка `_agent_dict`

### Что
- Строка 1391: `(a.id, a.name, a.specialization, a.description)` — **нет `a.gender`**
- Строки 2000-2005: `_agent_dict = {'id': ..., 'name': ..., ...}` — **нет `gender`**
- `_detect_agent_is_female` (автономная_агент.py:8505) получает `None` → всегда `False` (мужской род)

### Исправление
1. В строке 1391 добавить `a.gender` в тупл:
   ```python
   'agents': [(a.id, a.name, a.specialization, a.description, a.gender) for a in agents],
   ```
2. Распаковать `gender` в строке ~1594 (цикл for):
   ```python
   for aid, aname, aspec, adesc, agender in agents:
   ```
3. В строке 2000-2005 добавить `gender` в `_agent_dict`:
   ```python
   _agent_dict = {
       'id': _agent_id,
       'name': _agent_name_db,
       'specialization': _agent_spec,
       'description': _agent_desc,
       'gender': _agender,
   }
   ```

---

## Root Cause 3: python_code / user_api_keys / tools_allowed не передаются

### Где
Там же — строки 1391 и 2000-2005

### Что
`_exec_agent_for_director` использует:
- `agent.get('user_api_keys', '')` — для интеграций (строка 8621, 8677, 9571, 10551)
- `agent.get('python_code', '')` — для запуска скрипта (строка 8923, 9556)
- `agent.get('tools_allowed', '')` — для списка инструментов (строка 8624, 9663, 9694)

Без них агент:
- Не может запустить свой python-скрипт
- Не имеет API-ключей (email, GitHub, и т.д.)
- Не имеет списка разрешённых инструментов

### Исправление
1. В строке 1391 расширить тупл:
   ```python
   'agents': [(a.id, a.name, a.specialization, a.description, a.gender,
                a.python_code, a.user_api_keys, a.tools_allowed) for a in agents],
   ```
2. Распаковать в цикле:
   ```python
   for aid, aname, aspec, adesc, agender, apython, akeys, atools in agents:
   ```
3. В `_agent_dict` добавить все поля:
   ```python
   _agent_dict = {
       'id': _agent_id,
       'name': _agent_name_db,
       'specialization': _agent_spec,
       'description': _agent_desc,
       'gender': _agender,
       'python_code': _apython,
       'user_api_keys': _akeys,
       'tools_allowed': _atools,
   }
   ```

---

## Root Cause 4: Нет ASI-саммари после цикла

### Что
После завершения всех агентов (после `asyncio.gather`) нет сводки от ASI о том, что было сделано за цикл.

### Исправление (опционально)
Добавить после `await asyncio.gather(*_tasks)`:
```python
# ── ASI-саммари: что сделано за цикл ──
try:
    _summary_prompt = (
        f"Ты — ASI-координатор. Завершён цикл для пользователя.\n"
        f"Цели:\n{goals_text}\n\n"
        f"Назначенные задачи:\n{_assignments_text}\n\n"
        f"Напиши короткую сводку (2-3 предложения) что сделано за цикл."
    )
    ... AI call ...
    _save_interaction_for_director(user_db_id, _summary_text)
except Exception:
    pass
```

---

## План действий (в порядке приоритета)

| № | Что | Файл | Строки |
|---|-----|------|--------|
| 1 | Добавить `gender`, `python_code`, `user_api_keys`, `tools_allowed` в тупл agents | office_engine.py | 1391 |
| 2 | Распаковать новые поля в цикле for | office_engine.py | ~1594 |
| 3 | Добавить все поля в `_agent_dict` для `_exec_agent_for_director` | office_engine.py | 2000-2005 |
| 4 | Сохранять отчёт агента через `_save_interaction_for_director` после выполнения | office_engine.py | после 2008 |
| 5 | (Опционально) ASI-саммари после цикла | office_engine.py | после 2025 |
