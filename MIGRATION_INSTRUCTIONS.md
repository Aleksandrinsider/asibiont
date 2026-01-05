# Инструкция по сбросу current_time на Railway

## Проблема
В профилях пользователей сохранено статическое значение `current_time = "18:36"`, которое переопределяет реальное время и вызывает баги в AI responses.

## Решение
1. **Изменения в коде** (уже задеплоены):
   - Удалена логика сохранения `current_time` в БД
   - Исправлены deprecation warnings с `datetime.utcnow()`
   - AI теперь всегда использует реальное текущее время

2. **Нужно выполнить миграцию на Railway**:

### Вариант 1: Через Railway CLI (если установлен)
```bash
railway run python reset_current_time.py
```

### Вариант 2: Через Railway Shell (в веб-интерфейсе)
1. Открыть Railway Dashboard → Project → Environment → Shell
2. Выполнить:
```bash
python reset_current_time.py
```

### Вариант 3: Вручную через SQL
В Railway Dashboard → PostgreSQL → Connect → Query:
```sql
UPDATE user_profiles SET current_time = NULL WHERE current_time IS NOT NULL;
```

## Проверка
После миграции проверить, что AI возвращает правильное время:
- Отправить "привет" в чат
- AI должен ответить с текущим реальным временем, а не "18:36"

## Fallback
Если миграция не сработала, можно удалить колонку current_time из БД:
```sql
ALTER TABLE user_profiles DROP COLUMN IF EXISTS current_time;
```
