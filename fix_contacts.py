#!/usr/bin/env python3
# Скрипт для исправления порядка полей в контактах

def fix_contacts():
    with open('templates/dashboard_new.html', 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Исправляем порядок полей: добавляем компанию перед должностью
    # Заменяем паттерн: город -> должность -> [br] -> интересы -> компания
    # На: город -> компания -> должность -> интересы
    old_pattern = '''if (partner.city) {
                                    html += `<span class="task-meta">Город: ${partner.city}</span>`;
                                }
                                if (partner.position) {
                                    html += `<span class="task-meta">Должность: ${partner.position}</span>`;
                                }
                                if (partner.city || partner.position) {
                                    html += '<br>';
                                }
                                if (partner.interests) {
                                    html += `<span class="task-meta">Интересы: ${partner.interests}</span>`;
                                }
                                if (partner.company) {
                                    html += `<span class="task-meta">Компания: ${partner.company}</span>`;
                                }'''
    
    new_pattern = '''if (partner.city) {
                                    html += `<span class="task-meta">Город: ${partner.city}</span>`;
                                }
                                if (partner.company) {
                                    html += `<span class="task-meta">Компания: ${partner.company}</span>`;
                                }
                                if (partner.position) {
                                    html += `<span class="task-meta">Должность: ${partner.position}</span>`;
                                }
                                if (partner.interests) {
                                    html += `<span class="task-meta">Интересы: ${partner.interests}</span>`;
                                }'''
    
    # Заменяем все вхождения
    content = content.replace(old_pattern, new_pattern)
    
    with open('templates/dashboard_new.html', 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("Contacts order fixed!")

if __name__ == '__main__':
    fix_contacts()