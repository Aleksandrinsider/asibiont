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