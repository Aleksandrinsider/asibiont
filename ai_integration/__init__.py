"""
AI Integration модуль - разделенный на компоненты.
"""

from .tools import TOOLS
from .autonomous_agent import chat_with_ai
from .chat import generate_reminder, generate_result_check, generate_proactive_message, generate_daily_report, generate_overdue_reminder
from .memory import update_user_memory, encrypt_data, decrypt_data
from .handlers import (
    add_task, list_tasks, complete_task, reschedule_task,
    delegate_task, accept_delegated_task, reject_delegated_task, get_delegation_progress, cancel_delegation,
    edit_task, delete_task, get_task_details,
    find_partners, update_profile, smart_update_profile,
    create_subscription_payment, check_subscription_status,
    cancel_subscription, get_partners_list, check_delegation_deadlines, restore_task,
    analyze_group_opportunities, research_and_plan, analyze_situation_and_suggest_tasks, set_auto_post_time, get_weather_info, get_news_trends
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
    'encrypt_data',
    'decrypt_data',
    'delegate_task',
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
    'analyze_group_opportunities',
    'research_and_plan',
    'analyze_situation_and_suggest_tasks',
    'set_auto_post_time',
    'get_weather_info',
    'get_news_trends',
]
