"""Проверка обновленных функций"""
from ai_integration.tools import TOOLS

print("\n" + "="*60)
print("📋 ОБНОВЛЕННЫЙ СПИСОК ФУНКЦИЙ (20 шт.)")
print("="*60 + "\n")

categories = {
    "🎯 Управление задачами": [
        "add_task", "list_tasks", "complete_task", "reschedule_task",
        "edit_task", "delete_task", "get_task_details"
    ],
    "👤 Профиль": [
        "update_profile", "smart_update_profile", "show_profile", "update_user_memory"
    ],
    "🤝 Делегирование": [
        "delegate_task", "get_delegation_progress", 
        "accept_delegated_task", "reject_delegated_task"
    ],
    "👥 Контакты": [
        "find_partners", "find_relevant_contacts_for_task"
    ],
    "🌟 Premium": [
        "set_activity_alert", "set_contact_alert"
    ]
}

tools_by_name = {t["function"]["name"]: t for t in TOOLS}

for category, names in categories.items():
    print(f"{category}:")
    for name in names:
        if name in tools_by_name:
            desc = tools_by_name[name]["function"]["description"]
            # Берем первые 80 символов описания
            short_desc = desc[:80] + "..." if len(desc) > 80 else desc
            print(f"  ✓ {name}")
        else:
            print(f"  ❌ {name} - ОТСУТСТВУЕТ!")
    print()

print("="*60)
print(f"✅ Итого: {len(TOOLS)} функций загружено")
print("="*60)

# Проверяем новые премиум функции
print("\n🔍 Детали Premium функций:\n")
for name in ["set_activity_alert", "set_contact_alert"]:
    tool = tools_by_name[name]
    print(f"📌 {name}:")
    desc = tool["function"]["description"]
    # Извлекаем ключевые примеры
    if "Примеры:" in desc:
        examples = desc.split("Примеры:")[1].split(".")[0]
        print(f"   Примеры: {examples.strip()}")
    print()
