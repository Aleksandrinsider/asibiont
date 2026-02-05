"""
AI Integration модуль - разделенный на компоненты.
"""

from .tools import TOOLS
from .chat import chat_with_ai, generate_reminder, generate_result_check, generate_proactive_message, generate_daily_report, generate_overdue_reminder
from .memory import update_user_memory, encrypt_data, decrypt_data
from .handlers import (
    add_task, list_tasks, complete_task, reschedule_task,
    delegate_task_with_session, accept_delegated_task, reject_delegated_task, get_delegation_progress, cancel_delegation,
    edit_task, delete_task, get_task_details,
    find_partners, update_profile, smart_update_profile,
    create_subscription_payment, check_subscription_status,
    cancel_subscription, get_partners_list, check_delegation_deadlines, restore_task, update_user_memory_async
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
    'update_user_memory_async',
    'encrypt_data',
    'decrypt_data',
    'delegate_task_with_session',
    'accept_delegated_task',
    'reject_delegated_task',
    'get_delegation_progress',
    'cancel_delegation',
    'edit_task',
    'delete_task',
    'get_task_details',
    'find_partners',
    'update_profile',
    'smart_update_profile',
    'create_subscription_payment',
    'check_subscription_status',
    'cancel_subscription',
    'get_partners_list',
    'check_delegation_deadlines',
    'restore_task',
]
