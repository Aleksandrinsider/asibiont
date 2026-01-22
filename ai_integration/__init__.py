"""
AI Integration модуль - разделенный на компоненты.
"""

from .tools import TOOLS
from .chat import chat_with_ai, generate_reminder, generate_result_check, generate_proactive_message, generate_daily_report, generate_overdue_reminder
from .memory import update_user_memory, encrypt_data, decrypt_data
from .handlers import (
    add_task, list_tasks, complete_task, reschedule_task, get_task_advice, set_reminder,
    delegate_task, accept_delegated_task, reject_delegated_task, get_delegation_progress, cancel_delegation,
    edit_task, delete_task, set_priority, get_task_details,
    find_partners, update_profile, suggest_alternatives, delete_all_tasks,
    create_subscription_payment, check_subscription_status, brainstorm_ideas,
    cancel_subscription, get_partners_list, enrich_task_list_with_insights, check_delegation_deadlines, restore_task
)

__all__ = [
    'TOOLS',
    'chat_with_ai',
    'generate_reminder',
    'generate_result_check',
    'generate_proactive_message',
    'generate_daily_report',
    'generate_overdue_reminder',
    'add_task',
    'list_tasks',
    'complete_task',
    'reschedule_task',
    'get_task_advice',
    'set_reminder',
    'update_user_memory',
    'encrypt_data',
    'decrypt_data',
    'delegate_task',
    'accept_delegated_task',
    'reject_delegated_task',
    'get_delegation_progress',
    'cancel_delegation',
    'edit_task',
    'delete_task',
    'set_priority',
    'get_task_details',
    'find_partners',
    'update_profile',
    'suggest_alternatives',
    'delete_all_tasks',
    'create_subscription_payment',
    'check_subscription_status',
    'brainstorm_ideas',
    'cancel_subscription',
    'get_partners_list',
    'enrich_task_list_with_insights',
    'check_delegation_deadlines',
    'restore_task',
    'set_redis_client',
]
