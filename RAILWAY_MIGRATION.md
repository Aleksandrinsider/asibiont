# Railway Migration Commands

## Способ 1: Через Railway CLI

```bash
# 1. Установить Railway CLI (если еще не установлен)
npm i -g @railway/cli

# 2. Авторизоваться
railway login

# 3. Подключиться к проекту
railway link

# 4. Запустить миграцию
railway run python add_pending_delegator_field.py
```

## Способ 2: Через Railway Dashboard (SQL Console)

1. Открыть [Railway Dashboard](https://railway.app/dashboard)
2. Выбрать проект → PostgreSQL → Connect
3. Открыть **Query** консоль
4. Выполнить SQL напрямую:

```sql
-- Проверить существует ли поле
SELECT column_name 
FROM information_schema.columns 
WHERE table_name = 'tasks' 
AND column_name = 'pending_delegator_report';

-- Добавить поле (если не существует)
ALTER TABLE tasks 
ADD COLUMN IF NOT EXISTS pending_delegator_report BIGINT;

-- Проверить что поле добавлено
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'tasks' 
AND column_name = 'pending_delegator_report';
```

## Способ 3: Через код (автоматическая миграция при деплое)

Добавить в `main.py` перед запуском сервера:

```python
# В начале main.py после импортов
from sqlalchemy import inspect, text

def ensure_pending_delegator_field():
    """Ensure pending_delegator_report field exists in tasks table"""
    try:
        engine = create_engine(DATABASE_URL)
        inspector = inspect(engine)
        
        # Check if tasks table exists
        if 'tasks' not in inspector.get_table_names():
            logger.info("Tasks table doesn't exist yet, skipping migration")
            return
        
        # Check if column exists
        columns = [col['name'] for col in inspector.get_columns('tasks')]
        
        if 'pending_delegator_report' not in columns:
            logger.info("Adding pending_delegator_report column to tasks table...")
            with engine.connect() as conn:
                conn.execute(text("""
                    ALTER TABLE tasks 
                    ADD COLUMN pending_delegator_report BIGINT
                """))
                conn.commit()
            logger.info("✅ Successfully added pending_delegator_report column")
        else:
            logger.info("✅ pending_delegator_report column already exists")
            
    except Exception as e:
        logger.error(f"Error during migration: {e}")

# В функции main() ПЕРЕД app.run_app:
if __name__ == '__main__':
    # ... существующий код ...
    
    # Применяем миграцию перед запуском
    ensure_pending_delegator_field()
    
    web.run_app(app, host='0.0.0.0', port=PORT)
```

## Проверка после миграции

```sql
-- Посмотреть структуру таблицы tasks
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'tasks'
ORDER BY ordinal_position;

-- Проверить что поле работает
SELECT id, title, pending_delegator_report 
FROM tasks 
LIMIT 5;
```

## Откат миграции (если нужно)

```sql
-- Удалить поле
ALTER TABLE tasks 
DROP COLUMN IF EXISTS pending_delegator_report;
```
