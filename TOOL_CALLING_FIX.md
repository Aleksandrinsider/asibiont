# 🔧 FIX: Tool Calling Не Работает

## 🔍 КОРНЕВАЯ ПРИЧИНА

**Autonomous agent НЕ использует native tool calling API DeepSeek!**

### Текущий подход (СЛОМАН):
```
1. plan_strategy → AI возвращает JSON текст {"needs_tools": true/false, "tools": [...]}
2. execute_actions → выполняет actions из JSON
3. reflect_and_respond → формирует ответ
```

### Проблема:
- `call_ai()` в [autonomous_agent.py](ai_integration/autonomous_agent.py#L163-L183) **НЕ передает `tools` parameter** в API
- AI **решает сам** нужны ли инструменты через текстовый промпт
- AI **часто ошибается** и возвращает `"needs_tools": false` даже когда нужно
- Результат: **0 вызовов инструментов из 20 попыток** в тесте

### Код проблемы:
```python
# autonomous_agent.py, строка 163
async def call_ai(self, messages, **kwargs):
    data = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 2000,
        **kwargs  # НЕТ tools parameter!
    }
    # Вызов без tools - DeepSeek НЕ знает о tool_calls
```

---

## ✅ РЕШЕНИЕ 1: HYBRID APPROACH (РЕКОМЕНДУЕТСЯ)

**Смешанный подход: планирование + native tool calling**

### Как работает:
1. **Планирование** (текущее) - AI определяет intent
2. **Native tool calling** - DeepSeek API выбирает tools автоматически
3. **Execution** - выполняем tool_calls из API response
4. **Response** - AI формулирует ответ с результатами

### Преимущества:
- ✅ Сохраняет проактивную логику и анализ контекста
- ✅ Использует мощь DeepSeek для выбора инструментов
- ✅ Автоматическое определение параметров
- ✅ Меньше hallucinations ("Создал задачу" без реального вызова)

### Изменения:

#### 1. Добавить tools в call_ai:
```python
# autonomous_agent.py, строка 163
async def call_ai(self, messages, use_tools=False, **kwargs):
    """Универсальный вызов AI API с опциональными tools"""
    from .tools import TOOLS  # Импортируем готовые TOOLS
    
    data = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 2000,
        **kwargs
    }
    
    # Добавляем tools если нужно
    if use_tools:
        data["tools"] = TOOLS
        data["tool_choice"] = "auto"  # DeepSeek сам решает
    
    # ... остальное как есть
```

#### 2. Использовать tools в reflect_and_respond:
```python
# autonomous_agent.py, строка ~580
async def reflect_and_respond(self, user_message, plan, execution_results, context=None, user_id=None):
    """
    ШАГ 3: AI рефлексирует И вызывает инструменты при необходимости
    """
    
    # ... существующий код для формирования промпта ...
    
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_message}
    ]
    
    # КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ: добавляем use_tools=True
    response = await self.call_ai(messages, use_tools=True, temperature=0.5)
    
    # Проверяем tool_calls в ответе
    message = response['choices'][0]['message']
    
    if message.get('tool_calls'):
        # AI ХОЧЕТ ВЫЗВАТЬ ИНСТРУМЕНТЫ!
        logger.info(f"[AGENT] AI requested {len(message['tool_calls'])} tool calls")
        
        # Извлекаем actions из tool_calls
        actions = []
        for tool_call in message['tool_calls']:
            func = tool_call['function']
            actions.append({
                'tool': func['name'],
                'params': json.loads(func['arguments']),
                'reason': f"AI decision for {func['name']}"
            })
        
        # ВЫПОЛНЯЕМ ИНСТРУМЕНТЫ
        new_results = await self.execute_actions(actions, user_id)
        execution_results.extend(new_results)
        
        # ПОВТОРНЫЙ ВЫЗОВ AI с результатами
        # Добавляем tool_calls и их результаты в messages
        messages.append(message)  # Assistant message с tool_calls
        
        for i, tool_call in enumerate(message['tool_calls']):
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call['id'],
                "content": json.dumps(new_results[i]['result'] if new_results[i]['success'] else {'error': new_results[i]['error']})
            })
        
        # Финальный ответ БЕЗ tools
        final_response = await self.call_ai(messages, use_tools=False, temperature=0.5)
        content = final_response['choices'][0]['message']['content']
    else:
        # Просто текстовый ответ
        content = message.get('content', '')
    
    # ... остальное как есть
```

#### 3. Упростить plan_strategy:
```python
# autonomous_agent.py, строка ~187
async def plan_strategy(self, user_message, user_id, context=None):
    """
    ШАГ 1: Анализ интента (БЕЗ планирования инструментов)
    Теперь tools вызываются в reflect_and_respond через native API
    """
    # ... существующий код для получения контекста ...
    
    # УПРОЩЕННЫЙ ПРОМПТ - только определяем нужна ли проактивность
    planning_prompt = f"""Определи тип запроса.

КОНТЕКСТ: {context_info}

Запрос: "{user_message}"

JSON:
{{
  "intent_type": "command"|"question"|"task_management"|"general_chat",
  "needs_context_analysis": true если нужно анализировать задачи/профиль для ответа,
  "proactive_hints": "краткие подсказки для AI что предложить"
}}

Примеры:
- "привет" → {{"intent_type": "general_chat", "needs_context_analysis": false}}
- "мои задачи" → {{"intent_type": "task_management""needs_context_analysis": true}}
- "создай задачу X" → {{"intent_type": "command", "needs_context_analysis": false}}
"""
    
    # Вызываем БЕЗ tools
    response = await self.call_ai(messages, use_tools=False, temperature=0.3)
    
    # Парсим и возвращаем упрощенный план
    # Теперь AI SAM выберет tools в reflect_and_respond
```

### Результат:
- ✅ AI автоматически вызовет add_task когда пользователь скажет "создай задачу"
- ✅ Нет hallucinations "Создал задачу" без реального вызова
- ✅ DeepSeek сам определяет нужные параметры
- ✅ Сохраняется проактивная логика из plan_strategy

---

## 🔄 РЕШЕНИЕ 2: ROLLBACK К СТАРОМУ CHAT.PY (БЫСТРОЕ)

**Вернуться к простому подходу с native tool calling**

### Как работает:
1. User message → AI с tools
2. AI решает какие tools вызвать
3. Выполняем tool_calls
4. AI формирует ответ

### Преимущества:
- ✅✅ Быстро - уже работало раньше
- ✅ Просто - меньше кода
- ✅ Надежно - проверено

### Недостатки:
- ❌ Теряем проактивную логику
- ❌ Теряем кэширование
- ❌ Теряем self-learning

### НЕ РЕКОМЕНДУЕТСЯ - потеряем функционал

---

## 📊 ПЛАН ВНЕДРЕНИЯ (Решение 1)

### День 1 (СЕГОДНЯ):
1. ✅ Диагностика - выявлена корневая причина
2. ⏳ Добавить `use_tools` parameter в `call_ai()`
3. ⏳ Передать TOOLS в API когда `use_tools=True`
4. ⏳ Обработать `tool_calls` в `reflect_and_respond()`

### День 2:
5. Упростить `plan_strategy` - убрать планирование tools
6. Добавить логирование tool_calls
7. Протестировать: `python test_dialogue_live.py`

### День 3:
8. Проверить metrics: tool_calls > 0
9. Убедиться что задачи создаются в БД
10. Финальное тестирование 20 итераций

---

## 🎯 ОЖИДАЕМЫЙ РЕЗУЛЬТАТ

### До исправления:
```
Tool calls: 0/20 (0%) ❌❌❌
Tasks created: 0 ❌
Agent says: "Создал задачу" (но не создал)
```

### После исправления:
```
Tool calls: 12/20 (60%) ✅✅
Tasks created: 8 ✅
Agent says: "Создал задачу" (и реально создал!)
```

---

## 💡 ДОПОЛНИТЕЛЬНЫЕ УЛУЧШЕНИЯ

После fix:

1. **Логирование tool_calls:**
```python
logger.info(f"[TOOL_CALL] {func_name}({params}) → {result}")
```

2. **Metric tracking:**
```python
self.tool_call_stats = {
    'total_calls': 0,
    'successful': 0,
    'failed': 0,
    'by_tool': {}
}
```

3. **Fallback если AI не вызвал инструмент:**
```python
# Если пользователь явно попросил создать задачу,
# но AI не вызвал add_task - force call
if 'созда' in user_message.lower() and 'задач' in user_message.lower():
    if not any(tc['function']['name'] == 'add_task' for tc in tool_calls):
        logger.warning("[FALLBACK] Forcing add_task call")
        # Force tool call
```

---

## ❗ КРИТИЧЕСКИЕ МОМЕНТЫ

1. **Обязательно тестировать после каждого изменения**
2. **Логировать ВСЕ tool_calls и их результаты**
3. **Проверять что задачи реально создаются в БД**
4. **Сравнить metrics до/после**

---

## ✅ ЧЕКЛИСТ ГОТОВНОСТИ К ДЕПЛОЮ

- [ ] Tool calls работают (> 50% requests)
- [ ] Задачи создаются в БД
- [ ] Нет hallucinations ("Создал" без реального вызова)
- [ ] Логирование tool_calls работает
- [ ] Тесты прошли успешно
- [ ] Response length < 300 символов
- [ ] Metrics собираются корректно
