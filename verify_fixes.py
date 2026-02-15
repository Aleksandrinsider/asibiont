"""Quick verification of bug fixes"""
import os
os.environ['LOCAL'] = '1'
os.environ['DEEPSEEK_API_KEY'] = 'test'
os.environ['BOT_TOKEN'] = 'test:token'
os.environ['ENCRYPTION_KEY'] = 'dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXk='

import inspect

# Test 1: research_topic default depth
from ai_integration.handlers import research_topic
sig = inspect.signature(research_topic)
depth_param = sig.parameters['depth']
assert depth_param.default == 'full', f'depth default is {depth_param.default}'
print(f'1. research_topic depth default: {depth_param.default} ✅')

# Test 2: XML cleaning
from ai_integration.utils import clean_technical_details
xml_test = 'Вот результат. <function_calls><function_call><invoke name="research_topic"><arg name="query">test</arg></invoke></function_call></function_calls>'
cleaned = clean_technical_details(xml_test)
assert '<function_calls>' not in cleaned
assert '<invoke' not in cleaned
print(f'2. XML cleaning: "{cleaned.strip()}" ✅')

# Test 3: thinking tags
thinking_test = '<thinking> Пользователь хочет узнать... </thinking>Вот ответ.'
cleaned2 = clean_technical_details(thinking_test)
assert '<thinking>' not in cleaned2
assert 'Вот ответ' in cleaned2
print(f'3. Thinking cleanup: "{cleaned2.strip()}" ✅')

# Test 4: call function XML
call_test = 'Сейчас проверю. <call function="research_topic"><parameter name="query">AI тренды</parameter></call> Результат готов.'
cleaned3 = clean_technical_details(call_test)
assert '<call' not in cleaned3
assert '<parameter' not in cleaned3
print(f'4. Call function cleanup: "{cleaned3.strip()}" ✅')

# Test 5: Anti-repetition rule
from ai_integration.system_prompt import get_system_prompt_template
prompt = get_system_prompt_template()
assert 'Отлично' in prompt
assert 'НЕЛЬЗЯ ИСПОЛЬЗОВАТЬ В НАЧАЛЕ' in prompt
print(f'5. Anti-repetition with explicit Отлично ban ✅')

print('\nAll fixes verified!')
