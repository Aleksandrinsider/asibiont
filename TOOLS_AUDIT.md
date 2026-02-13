# Аудит функций бота (69 инструментов)

## ✅ ЯДРО - ОБЯЗАТЕЛЬНЫЕ (15):
1. **add_task** - создание задач 🔥
2. **list_tasks** - список задач 🔥  
3. **complete_task** - завершение задач 🔥
4. **edit_task** - редактирование задач
5. **delete_task** - удаление задач
6. **reschedule_task** - перенос задач
7. **skip_task** - пропуск задач
8. **restore_task** - восстановление
9. **get_task_details** - детали задачи
10. **find_partners** - поиск партнеров 🔥
11. **get_partners_list** - список контактов
12. **show_profile** - показ профиля 🔥
13. **update_profile** - обновление профиля
14. **smart_update_profile** - умное обновление профиля 🔥
15. **analyze_situation_and_suggest_tasks** - анализ ситуации 🔥

## 🎯 ПОЛЕЗНЫЕ - PREMIUM (12):
16. **delegate_task** - делегирование задач 💎
17. **accept_delegated_task** - принятие делегирования
18. **reject_delegated_task** - отклонение делегирования  
19. **get_delegation_progress** - прогресс делегирования
20. **cancel_delegation** - отмена делегирования
21. **find_relevant_contacts_for_task** - релевантные контакты для задачи 💎
22. **research_topic** - глубокое исследование темы 💎
23. **quick_topic_search** - быстрый поиск по теме 💎
24. **get_news_trends** - новости и тренды 💎
25. **research_and_plan** - исследование + планирование
26. **suggest_trends_and_opportunities** - предложения трендов
27. **check_topic_relevance** - проверка релевантности темы

## 📝 ВСПОМОГАТЕЛЬНЫЕ (10):
28. **check_time_conflicts** - проверка конфликтов по времени
29. **analyze_tasks** - анализ задач
30. **update_user_memory_async** - обновление памяти
31. **analyze_group_opportunities** - анализ групповых возможностей
32. **generate_delegation_notification** - уведомления о делегировании
33. **generate_progress_request** - запросы прогресса
34. **generate_delegation_response_notification** - ответы на делегирование
35. **schedule_delegation_monitoring** - мониторинг делегирования
36. **check_delegation_deadlines** - проверка дедлайнов делегирования
37. **check_subscription_status** - статус подписки

## 🚀 МАРКЕТИНГ - PREMIUM (7):
38. **generate_marketing_content** - генерация маркетингового контента 💎
39. **set_content_strategy** - стратегия контента
40. **toggle_autonomous_feature** - переключение автономных функций
41. **publish_to_telegram** - публикация в Telegram
42. **set_activity_alert** - алерты по активностям
43. **set_contact_alert** - алерты по контактам
44. **set_auto_post_time** - время авто-постинга

## 📊 ИНФОРМАЦИЯ (4):
45. **get_weather_info** - погода
46. **get_stock_info** - акции  
47. **get_news_info** - новости
48. **web_search** - веб-поиск 💎

## 💳 ПЛАТЕЖИ (2):
49. **create_subscription_payment** - создание платежа
50. **cancel_subscription** - отмена подписки

## ❌ ДУБЛИКАТЫ или УСТАРЕВШИЕ (19):

### Дубликаты функций:
51. **delete_all_tasks** - ⚠️ ОПАСНО! Удаляет ВСЕ задачи - лучше удалить
52. **delete_task_sync** - дубликат delete_task (синхронная версия)
53. **delegate_task_with_session** - дубликат delegate_task
54. **check_time_conflicts_sync** - дубликат async версии (устарела)
55. **update_user_memory** - дубликат update_user_memory_async

### Дубликаты get_weather/stock/news (удалены строки 6794-6952):
56-59. **get_weather_info** (2 раза), **get_stock_info** (2 раза), **get_news_info** (2 раза), **web_search** (2 раза)

### Внутренние вспомогательные (не должны быть в tools):
60. **get_tier_priority** - внутренняя функция
61. **find_nearest_free_slot** - внутренняя функция
62. **_merge_similar_goals** - приватная функция
63. **_add_to_list_field** - приватная функция  
64. **generate_delegation_notification_async** - дубликат
65. **generate_delegation_response_notification_async** - дубликат
66. **check_delegation_deadlines** - внутренний cronjob
67. **schedule_delegation_monitoring** - внутренний сервис
68. **delete_task_sync** - синхронная версия (устарела)
69. **update_user_memory** - синхронная версия (устарела)

---

## 🎯 РЕКОМЕНДАЦИИ:

### УДАЛИТЬ (минус 19 функций):
1. **delete_all_tasks** - опасная функция
2. **delete_task_sync** - дубликат
3. **delegate_task_with_session** - дубликат
4. **check_time_conflicts_sync** - устарела
5. **update_user_memory** - дубликат
6-9. Дубликаты get_weather/stock/news/web_search (строки 6794+)
10-19. Приватные и внутренние функции (_merge_similar_goals, get_tier_priority, и т.д.)

### ОБЪЕДИНИТЬ (минус 5 функций):
- **research_topic + quick_topic_search** → одна функция с параметром depth
- **check_topic_relevance** → встроить в research_topic
- **research_and_plan** → встроить в research_topic
- **generate_delegation_notification** → внутренняя, не для AI
- **generate_progress_request** → внутренняя, не для AI

### ИТОГО:
**69 функций → 45 функций** (минус 24)

**Оптимальный набор:**
- **LIGHT** (бесплатный): 15 функций (ядро)
- **STANDARD**: 25 функций (ядро + поиск/новости)  
- **PREMIUM**: 45 функций (все)

**Реальная польза:**
- Меньше функций = AI быстрее выбирает нужную
- Меньше ошибок при парсинге  
- Ниже стоимость API вызова (меньше токенов в промпте)
