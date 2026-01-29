# Жизненный цикл делегированных задач

## ✅ Исправлено в последнем обновлении

### Проблемы:
1. ❌ При принятии задачи показывалось "от @aleksandrinsider" вместо "@sport_alex"
2. ❌ После принятия задача исчезала из списка
3. ❌ Раздел "Поручил я" был пустым
4. ❌ status не менялся при принятии задачи

### Решения:
1. ✅ Используется `task.delegated_by` вместо `task.user_id` для отображения делегировавшего
2. ✅ При принятии `status='in_progress'` вместо оставления `'pending'`
3. ✅ Запросы используют `Task.delegated_by == user.id` для раздела "Поручил я"
4. ✅ Задачи остаются видимыми в фильтрах `status != 'completed' && status != 'rejected'`

---

## 📋 Жизненный цикл задачи

### 1️⃣ Создание и делегирование
**Александр** создаёт задачу и делегирует **Марии**

```python
Task(
    user_id=maria.id,           # Получатель задачи
    delegated_by=aleksandr.id,  # Кто делегировал
    delegated_to_username='fitness_maria',
    status='pending',           # ⏳ Ожидает
    delegation_status='pending' # ⏳ Ожидает принятия
)
```

**Видимость:**
- У Марии: `Поручили мне` (контакты), `Назначенные мне` (задачи)
- У Александра: `Поручил я` (контакты)

---

### 2️⃣ Принятие задачи
**Мария** нажимает кнопку **"Принять задачу"**

```python
task.status = 'in_progress'       # 🔄 В работе
task.delegation_status = 'accepted' # ✅ Принята
```

**Видимость:**
- У Марии: `Поручили мне`, `Назначенные мне`, `Все задачи`
- У Александра: `Поручил я` (видит статус "Принята")
- ✅ Задача **НЕ пропадает**

---

### 3️⃣ Выполнение задачи
**Мария** отмечает задачу как **выполненную**

```python
task.status = 'completed'         # ✅ Завершена
task.delegation_status = 'accepted' # ✅ (остаётся)
```

**Видимость:**
- У Марии: `Завершённые`
- У Александра: `Поручил я` (видит что выполнена)
- ❌ Исчезает из активных задач

---

### 4️⃣ Отклонение задачи
**Мария** нажимает **"Отклонить задачу"**

```python
task.status = 'rejected'           # ❌ Отклонена
task.delegation_status = 'rejected' # ❌ Отклонена
```

**Видимость:**
- ❌ Не показывается в активных задачах
- Может быть в истории/архиве

---

## 🔍 Фильтры задач

### Раздел "Поручили мне" (Контакты)
```sql
WHERE delegated_to_username = 'aleksandrinsider'
  AND delegation_status IN ('pending', 'accepted')
  AND status NOT IN ('deleted', 'rejected')
```
→ Показывает контакты, которые делегировали задачи

### Раздел "Назначенные мне" (Задачи)
```javascript
filter(t => t.status !== 'completed' 
         && t.status !== 'rejected' 
         && t.is_delegated 
         && t.title.includes(' - Делегирована от @'))
```
→ Показывает активные делегированные задачи (`pending` и `in_progress`)

### Раздел "Поручил я" (Контакты)
```sql
WHERE delegated_by = 1  -- aleksandr.id
  AND delegated_to_username IS NOT NULL
  AND delegation_status IN ('pending', 'accepted')
```
→ Показывает контакты, которым я делегировал задачи

### Раздел "Все задачи"
```javascript
filter(t => t.status !== 'completed' 
         && t.status !== 'rejected')
```
→ Показывает все активные задачи (свои + делегированные)

---

## 🐛 Исправленные баги

### 1. Неправильное отображение делегировавшего
**Было:** `task.user_id` → показывало получателя  
**Стало:** `task.delegated_by` → показывает кто делегировал

```python
# main.py:5852
creator = session_db.query(User).filter_by(id=task.delegated_by).first()
```

### 2. Исчезновение задачи после принятия
**Было:** `task.delegation_status = 'accepted'` (status оставался pending)  
**Стало:** `task.status = 'in_progress'` + `task.delegation_status = 'accepted'`

```python
# main.py:4909
task.status = 'in_progress'
task.delegation_status = 'accepted'
```

### 3. Пустой раздел "Поручил я"
**Было:** `Task.user_id == user.id` → искало задачи ДЛЯ меня  
**Стало:** `Task.delegated_by == user.id` → ищет задачи, которые Я делегировал

```python
# main.py:1902, 3113, 4093
Task.delegated_by == user.id
```

---

## 📊 Статусы задач

| Status | Описание | Видна в фильтрах |
|--------|----------|------------------|
| `pending` | Ожидает выполнения | ✅ Все активные |
| `in_progress` | В работе (принята) | ✅ Все активные |
| `completed` | Выполнена | ✅ Только "Завершённые" |
| `rejected` | Отклонена | ❌ Не показывается |
| `deleted` | Удалена | ❌ Не показывается |
| `skipped` | Пропущена | ⚠️ Зависит от фильтра |

## 📊 Статусы делегирования

| delegation_status | Описание | Показывается |
|-------------------|----------|--------------|
| `pending` | Ожидает принятия | ✅ Да |
| `accepted` | Принята исполнителем | ✅ Да |
| `rejected` | Отклонена исполнителем | ❌ Нет |
| `None` | Не делегирована | N/A |

---

## ✅ Итоговое состояние БД

После всех исправлений:

```
📋 Задачи @aleksandrinsider:
├─ От @sport_alex (Поручили мне):
│  ├─ ⏳ Проверить регистрацию на марафон (pending/pending)
│  ├─ ⏳ Купить экипировку (pending/pending)
│  ├─ ⏳ Записаться на тренировку (pending/pending)
│  ├─ ⏳ Заказать справку (pending/pending)
│  └─ 🔄 План питания (in_progress/accepted) ✅
│
└─ Для @fitness_maria (Поручил я):
   ├─ ⏳ План тренировок (pending/pending)
   ├─ ⏳ Консультация по питанию (pending/pending)
   ├─ ⏳ Отчет по занятиям (pending/pending)
   ├─ ⏳ Групповая тренировка (pending/pending)
   └─ ⏳ Обновить сертификаты (pending/pending)
```

**Формат:** status/delegation_status

---

## 🚀 Деплой

Все изменения задеплоены на Railway:
- Commit: `06ba362` - Fix delegation task lifecycle
- Commit: `e81ccaa` - Fix delegation bugs (use delegated_by)
- Commit: `4c50b08` - Fix 'Поручил я' section
- Commit: `732c955` - Fix delegated tasks display

✅ Проверено и работает!
