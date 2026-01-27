async def generate_response(action: str, **kwargs):
    """
    Generate natural language response.
    For PoC, simple templates.
    TODO: Integrate with AI for better responses.
    """
    if action == 'task_created':
        task = kwargs.get('task')
        return f"✅ Задача создана: {task.title if hasattr(task, 'title') else 'новая задача'}"
    elif action == 'task_completed':
        return "✅ Задача выполнена!"
    elif action == 'task_deleted':
        return "🗑️ Задача удалена"
    else:
        return kwargs.get('message', 'Готово')

async def generate_clarification_response(original_message: str):
    """
    Generate a clarification question when message is unclear.
    """
    clarification_templates = [
        f"Извините, не совсем понял ваш запрос \"{original_message}\". Можете уточнить, что именно вы имеете в виду?",
        f"Ваш запрос \"{original_message}\" кажется неоднозначным. Что конкретно вы хотите сделать?",
        f"Не уверен, что правильно понял \"{original_message}\". Расскажите подробнее, пожалуйста.",
        f"Простите, \"{original_message}\" - это не совсем ясно. Можете перефразировать или дать больше деталей?"
    ]

    # Simple selection based on message length (can be improved with AI)
    import random
    return random.choice(clarification_templates)