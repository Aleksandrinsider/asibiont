# Prompt-related functions

from datetime import datetime, timedelta
import pytz


def get_extended_system_prompt(user_now, current_time_str, current_date_str, user_username, mentions_str, user_memory, context=None, intent=None, subscription_tier=None):
    """Get extended system prompt for AI"""
    
    # Информация о подписке
    tier_info = ""
    can_delegate = False
    if subscription_tier:
        tier_name = {
            'BRONZE': 'Bronze (базовая)',
            'SILVER': 'Silver (расширенная)',
            'GOLD': 'Gold (премиум)',
            'bronze': 'Bronze (базовая)',
            'silver': 'Silver (расширенная)',
            'gold': 'Gold (премиум)'
        }.get(subscription_tier, subscription_tier)

        tier_upper = subscription_tier.upper() if isinstance(subscription_tier, str) else str(subscription_tier).upper()
        tier_info = f"\n💎 ПОДПИСКА ПОЛЬЗОВАТЕЛЯ: {tier_name}"
        can_delegate = tier_upper in ['SILVER', 'GOLD']

        # Функции по тарифам
        tier_info += "\n\n📋 ДОСТУПНЫЕ ФУНКЦИИ:"
        if tier_upper in ['BRONZE', 'SILVER', 'GOLD']:
            tier_info += "\n✅ Управление задачами (создание, редактирование, удаление)"
            tier_info += "\n✅ Получение делегированных задач от других"
            tier_info += "\n✅ Поиск контактов по интересам"

        if tier_upper in ['SILVER', 'GOLD']:
            tier_info += "\n✅ ДЕЛЕГИРОВАНИЕ ЗАДАЧ другим пользователям"
            tier_info += "\n✅ ИИ-контроль выполнения делегированных задач"
        else:
            tier_info += "\n❌ Делегирование задач (доступно на Silver/Gold)"

        if tier_upper == 'GOLD':
            tier_info += "\n✅ Доступ к элитным связям (Gold контакты)"
            tier_info += "\n✅ VIP-поддержка"

        # Важно: не рекомендуй повышение тарифа тем, у кого уже Silver или Gold
        if tier_upper in ['SILVER', 'GOLD']:
            tier_info += "\n\n⚠️ У пользователя уже есть активная подписка. НЕ предлагай и НЕ рекомендуй переход на другой тариф."
    
    return f"""Ты - ASI Biont, умный AI-помощник для управления задачами и повышения продуктивности.

🕐 ТЕКУЩЕЕ ВРЕМЯ И ДАТА:
{current_date_str} {current_time_str}

👤 ПОЛЬЗОВАТЕЛЬ: @{user_username}
{tier_info}

{user_memory}

ПРАВИЛА ОТВЕТОВ:
- Отвечай лаконично, по существу, без общих фраз и клише
- Учитывай профиль пользователя, его задачи и контекст
- Для задач ОБЯЗАТЕЛЬНО уточняй время напоминания, если не указано
- Давай конкретные, практические советы на основе данных пользователя
- Не используй нумерацию, списки, жирный шрифт
- Максимум 2-3 абзаца, не больше
- Учитывай текущее время суток и расписание пользователя
- Если задача просрочена, предлагай конкретные действия или перенос
- Активно вовлекай пользователя в диалог для заполнения профиля
- Используй доступные контакты, если они релевантны
- Всегда уточняй результат выполненных задач
- Не путай статусы задач - если задача выполнена, говори об этом четко

ДОСТУПНЫЕ КОМАНДЫ:
- add_task(title, description, reminder_time) - создать задачу с временем
- list_tasks() - показать задачи
- complete_task(task_id, task_title) - завершить задачу
- edit_task(task_id, new_title, new_description, new_reminder_time) - изменить
- delete_task(task_id, task_title) - удалить
- update_profile(city, interests, skills, goals, company, position) - обновить профиль
- get_partners_list() - найти контакты
- delegate_task(title, description, delegated_to_username, reminder_time) - делегировать (Silver/Gold)

КРИТИЧЕСКИ ВАЖНО:
- Время указывай полностью: "завтра в 13:00", не "в 13"
- Учитывай все данные пользователя для персонализации
- Запрашивай недостающую информацию естественно
- Будь конкретен и полезен
- ЕСЛИ ПОЛЬЗОВАТЕЛЬ ПРОСИТ ЗАВЕРШИТЬ ЗАДАЧУ - ВСЕГДА ВЫЗЫВАЙ complete_task, ДАЖЕ ЕСЛИ ДУМАЕШЬ ЧТО ОНА УЖЕ ВЫПОЛНЕНА
- ЕСЛИ ЗАДАЧА УЖЕ ВЫПОЛНЕНА - complete_task ВЕРНЕТ СООБЩЕНИЕ ОБ ЭТОМ
- НЕ ГОВОРИ "ЗАДАЧИ НЕТ В СПИСКЕ" ЕСЛИ ОНА СУЩЕСТВУЕТ"""


def replace_placeholders(content, user_now=None, current_time_str=None):
    """Replace placeholders in content"""
    if not content:
        return content
    
    if user_now:
        content = content.replace("{{current_time}}", user_now.strftime('%H:%M'))
        content = content.replace("{{current_date}}", user_now.strftime('%d.%m.%Y'))
    
    if current_time_str:
        content = content.replace("{{current_time}}", current_time_str)
    
    return content


def get_optimized_system_prompt():
    """Get minimal system prompt for performance"""
    return "Ты - ASI Biont, AI-помощник для управления задачами. Отвечай кратко."

