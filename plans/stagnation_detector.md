# Универсальный Детектор Стагнации (Stagnation Detector)

## Проблема

Агенты могут зацикливаться на непродуктивных паттернах:
- Неделями искать контакт одного человека без результата
- Много раз исследовать одну тему без конверсии в действие
- Повторять одни и те же dead-end подходы

Существующие детекторы (tool-level loop, cross-dispatch cycle) ловят **инструментальные повторения**, но не **паттерн стагнации** — когда инструменты разные, действий много, но прогресса нет.

## Ключевая идея

Отслеживать **транзишены состояния** (state transitions), а не сущности:

```
ПРОДУКТИВНО:  search → found contact → save_contact → send_email → reply_received → respond
               ↑ прогресс есть, состояние меняется

СТАГНАЦИЯ:    search → (ничего) → search → (ничего) → search → (ничего)
               ↑ состояние не меняется, хотя инструменты могут быть разными
```

## Архитектура

### 1. Три детектора в одной функции `_build_stagnation_warning()`

#### А. Детектор поиска без конверсии (Search Loop)
- Сканирует AgentActivityLog за 48ч
- Считает: search_count vs action_count vs outcome_count
- **Порог**: search_count >= 3 И action_count == 0 И outcome_count == 0
- **Исключение**: если есть reply_received (продуктивная переписка) — не warning

#### Б. Детектор повторяющихся целей (Target Redundancy)
- Смотрит на поле `target` в логах
- Если один и тот же target >= 3 раз ИЛИ тот же target + dead-end result >= 2 раз
- **Исключение**: если EmailOutreach.reply_text не пуст для этого target → активная переписка

#### В. Детектор стагнации целей (Goal Stagnation)
- Активные цели старше 7 дней без milestone/progress
- Проверяет Goal.progress_percentage и Goal.progress_notes за последние 3 дня

### 2. Интеграция в `_exec_agent_for_director()`
- Вызывается после блока саморефлексии (там же где сейчас entity saturation)
- Результат: синтезированный блок с конкретными рекомендациями

## Изменения в коде

### File: [`ai_integration/autonomous_agent.py`](ai_integration/autonomous_agent.py)

**Change 1**: Заменить `_build_entity_saturation_block()` на `_build_stagnation_warning()`
- Новый код: ~80 строк вместо ~110
- Сигнатура: `def _build_stagnation_warning(user_id: int, agent_name: str = '', task: str = '') -> str`
- Локация: те же строки (после `_get_agent_inline_think_token`)

**Change 2**: Обновить `_get_agent_inline_think_token()`
- Убрать упоминание "контакт упоминается ≥3 раз"
- Добавить общее правило: "Если 3+ поиска без конверсии — смени подход"

**Change 3**: Обновить инжекцию (строки ~10358-10362)
- `_entity_sat_block` → `_stagnation_block`
- `_build_entity_saturation_block` → `_build_stagnation_warning`

## Детальная реализация `_build_stagnation_warning()`

```python
def _build_stagnation_warning(user_id: int, agent_name: str = '', task: str = '') -> str:
    """
    Универсальный детектор стагнации.
    
    Анализирует за 48ч:
    1. Search-to-action ratio: много поисков, ноль действий
    2. Target redundancy: один и тот же target повторяется без результата
    3. Goal stagnation: активная цель без прогресса >7 дней
    
    Умеет отличать продуктивную переписку (есть reply) от зацикливания.
    Работает для любых целей и интеграций.
    """
    try:
        from models import Session as _SessSt, AgentActivityLog as _ALogSt, User as _USt, Goal as _GSt
        from datetime import datetime as _dtSt, timedelta as _tdSt
        
        _db = _SessSt()
        try:
            _u_st = _db.query(_USt).filter_by(telegram_id=user_id).first()
            _db_user_id = _u_st.id if _u_st else user_id
            _cutoff = _dtSt.utcnow() - _tdSt(hours=48)
            
            _logs = _db.query(_ALogSt).filter(
                _ALogSt.user_id == _db_user_id,
                _ALogSt.created_at >= _cutoff,
            ).order_by(_ALogSt.created_at.desc()).limit(80).all()
            
            if len(_logs) < 3:
                return ''
            
            _warnings: list[str] = []
            _search_count = 0
            _action_count = 0
            _has_outcome = False
            _target_freq: dict[str, dict] = {}  # target -> {count, has_reply, last_result}
            
            # Классификация инструментов
            _SEARCH_TOOLS = {
                'web_search', 'research_topic', 'quick_topic_search',
                'find_relevant_contacts_for_task', 'find_and_message_relevant_users',
                'search_notes', 'check_emails', 'get_news_trends',
                'list_email_contacts',
            }
            _ACTION_TOOLS = {
                'send_outreach_email', 'send_email', 'save_email_contact',
                'create_post', 'publish_to_telegram', 'publish_to_discord',
                'generate_image', 'delegate_task', 'run_agent_action',
                'start_email_campaign', 'start_content_campaign',
                'save_note', 'reply_to_outreach_email', 'send_follow_up_email',
            }
            _OUTCOME_KEYWORDS = {
                'отправлен', 'sent', 'сохранен', 'saved',
                'опубликован', 'published', 'найдено', 'found',
                'готово', 'done', 'успешно', 'success',
                'result', 'результат', 'ответ', 'reply',
            }
            
            for _log in _logs:
                _tn = (_log.tool_name or '').strip()
                if _tn in _SEARCH_TOOLS:
                    _search_count += 1
                elif _tn in _ACTION_TOOLS:
                    _action_count += 1
                
                _res = (_log.result or '').lower()
                if any(kw in _res for kw in _OUTCOME_KEYWORDS):
                    _has_outcome = True
                
                # Сбор target'ов
                _tgt = (_log.target or '').strip().lower()
                if _tgt and len(_tgt) >= 3:
                    if _tgt not in _target_freq:
                        _target_freq[_tgt] = {'count': 0, 'has_reply': False, 'last_result': ''}
                    _target_freq[_tgt]['count'] += 1
                    if 'reply' in _res or 'ответ' in _res:
                        _target_freq[_tgt]['has_reply'] = True
                    if _res:
                        _target_freq[_tgt]['last_result'] = _res[:200]
            
            # ── Детектор 1: Search Loop ──
            if _search_count >= 3 and _action_count == 0 and not _has_outcome:
                _warnings.append(
                    f"🔍 {_search_count} поисков без единого действия и результата. "
                    "Ты собираешь данные, но не конвертируешь их. "
                    "Варианты: save_note → сохрани выводы, "
                    "send_outreach_email → напиши контакту, "
                    "add_task → создай шаги."
                )
            elif _search_count >= 5 and _action_count <= 1 and not _has_outcome:
                _warnings.append(
                    f"🔍 {_search_count} поисков, но только {_action_count} действие(й) и нет результата. "
                    "Данных уже достаточно — пора переходить к действию, а не искать ещё."
                )
            
            # ── Детектор 2: Target Redundancy ──
            # Сортируем по частоте
            _sorted_targets = sorted(
                _target_freq.items(),
                key=lambda x: (-x[1]['count'], x[0])
            )
            for _tgt_name, _tgt_data in _sorted_targets[:3]:
                if _tgt_data['count'] >= 3:
                    # Проверяем, есть ли активная переписка по этому контакту
                    _has_active_correspondence = False
                    try:
                        from models import EmailOutreach as _EOSt
                        _eo = _db.query(_EOSt).filter(
                            _EOSt.user_id == _db_user_id,
                            _EOSt.recipient_email.ilike(f'%{_tgt_name}%'),
                        ).order_by(_EOSt.updated_at.desc()).first()
                        if _eo and _eo.reply_text:
                            _has_active_correspondence = True
                    except Exception:
                        pass
                    
                    if not _has_active_correspondence:
                        _is_dead_end = any(kw in _tgt_data['last_result']
                                          for kw in ('не нашёл', 'тупик', 'не находится',
                                                    'нет доступа', 'not found', 'dead end'))
                        if _is_dead_end:
                            _warnings.append(
                                f"🎯 Контакт «{_tgt_name[:40]}» обработан {_tgt_data['count']} раз(а), "
                                f"результат: тупик. Сохрани выводы (save_note) и переключись."
                            )
                        else:
                            _warnings.append(
                                f"🎯 Контакт «{_tgt_name[:40]}» обработан {_tgt_data['count']} раз(а) "
                                f"без ответа. Если контакт не отвечает — переключись на другой приоритет."
                            )
            
            # ── Детектор 3: Goal Stagnation ──
            try:
                _active_goals = _db.query(_GSt).filter(
                    _GSt.user_id == _db_user_id,
                    _GSt.status == 'active',
                ).all()
                for _goal in _active_goals:
                    _age = (_dtSt.utcnow() - _goal.created_at).days if _goal.created_at else 0
                    if _age >= 7:
                        # Проверяем прогресс за последние 3 дня
                        _recent_goal_logs = _db.query(_ALogSt).filter(
                            _ALogSt.user_id == _db_user_id,
                            _ALogSt.created_at >= _dtSt.utcnow() - _tdSt(days=3),
                            _ALogSt.activity_type == 'goal_progress',
                        ).count()
                        if _recent_goal_logs == 0:
                            _pct = _goal.progress_percentage or 0
                            _warnings.append(
                                f"🎯 Цель «{_goal.title[:50]}» активна {_age} дней, прогресс {_pct}% "
                                f"без изменений за 3 дня. Разбей на подзадачи или пересмотри актуальность."
                            )
            except Exception:
                pass
            
            if not _warnings:
                return ''
            
            return (
                "\n\n⏳ ДЕТЕКТОР СТАГНАЦИИ (анализ за 48ч):\n"
                + '\n'.join(f'  • {w}' for w in _warnings[:4])
                + "\n\n📌 Если прогресса нет — смени подход кардинально. "
                "Не повторяй однотипные действия без результата. "
                "Попробуй другой канал, другую аудиторию или другую задачу.\n"
            )
            
        finally:
            _db.close()
    except Exception as _e:
        logger.debug('[STAGNATION] warning error: %s', _e)
        return ''
```

## Сравнение с существующими детекторами

| Детектор | Что ловит | Чего НЕ ловит |
|----------|-----------|---------------|
| Tool-level loop (`_check_agent_loop_risk`) | Те же tool+params ≥3x | Разные инструменты, same person |
| Cross-dispatch cycle | 3+ dispatches с теми же инструментами | Разные инструменты, no outcome |
| Self-reflection | Показывает 🔒✅⏳ | Не делает вывод |
| **Stagnation Detector (NEW)** | **Search без action, target redundancy, goal staleness** | **Productive correspondence** |

## Как отличить продуктивную переписку от зацикливания

1. **EmailOutreach.reply_text** — если не пуст, значит контакт ответил → активная переписка
2. **AgentActivityLog.result** — если содержит 'reply'/'ответ' → есть реакция
3. **Статус 'replied'** на email_outreach → контакт вовлечён

Если любой из этих признаков есть — warning НЕ выводится для этого target.

## Риски и митигации

| Риск | Митигация |
|------|-----------|
| False positive на первых запусках | Порог ≥3 логов перед анализом |
| Пропуск реальной стагнации из-за reply | Reply — объективный признак продуктивности |
| Нагрузка на БД | Один запрос, лимит 80 строк, кэш сессии |
