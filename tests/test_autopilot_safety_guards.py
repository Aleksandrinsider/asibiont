import ai_integration.autonomous_agent as aa_mod


def test_payment_guard_blocks_unverified_requisites():
    text = (
        "Готово. Вот реквизиты для перевода: карта 4276 3800 1234 5678, "
        "расчетный счет 40817810099910004312."
    )
    tool_blob = "{\"status\":\"ok\",\"note\":\"no payment tool used\"}"

    out, changed = aa_mod._guard_unverified_payment_details(text, tool_blob, "сделай пост")

    assert changed is True
    assert "4276" not in out
    assert "40817810099910004312" not in out
    assert "без подтверждённых данных" in out or "не отправляю" in out


def test_payment_guard_respects_user_no_requisites_rule():
    text = "Отправляю реквизиты: карта 5555 4444 3333 2222."
    tool_blob = "{\"provider\":\"yookassa\",\"payment_url\":\"https://pay.test\"}"

    out, changed = aa_mod._guard_unverified_payment_details(
        text,
        tool_blob,
        "не надо реквизиты в сообщениях",
    )

    assert changed is True
    assert "5555" not in out
    assert "ты просил" in out.lower() or "убрал" in out.lower()


def test_payment_guard_allows_verified_payment_context():
    text = "Счёт создан в ЮKassa, оплата по ссылке https://pay.test/invoice/42"
    tool_blob = "{\"provider\":\"yookassa\",\"invoice_url\":\"https://pay.test/invoice/42\"}"

    out, changed = aa_mod._guard_unverified_payment_details(text, tool_blob, "создай ссылку на оплату")

    assert changed is False
    assert out == text


def test_publish_guard_removes_false_success_when_channel_error():
    text = "Пост успешно опубликован в Telegram-канал. Также вижу, что нет доступа к каналу."
    tool_blob = "telegram-канал не настроен; bot was kicked from the channel chat; \"success\": false"

    out, changed = aa_mod._guard_publish_consistency(text, tool_blob)

    assert changed is True
    assert "успешно опубликован" not in out.lower()
    assert "пока не опубликовано" in out.lower() or "нет доступа" in out.lower()


def test_universal_policy_blocks_publish_tools_from_memory_rule():
    rules = ["Запомни: никогда не публикуй ничего без моего явного подтверждения"]

    blocked = aa_mod._resolve_forbidden_tools(rules, "сделай отчёт")

    assert 'create_post' in blocked
    assert 'publish_to_telegram' in blocked


def test_universal_policy_does_not_make_permanent_block_from_context_message():
    rules = []

    blocked = aa_mod._resolve_forbidden_tools(rules, "не отправляй письма и без email")

    assert blocked == {}


def test_universal_policy_allows_domain_when_user_explicitly_reenables():
    rules = ["не генерируй картинки"]

    blocked = aa_mod._resolve_forbidden_tools(rules, "можно картинки, сделай картинку к посту")

    assert 'generate_image' not in blocked


def test_contextual_block_only_for_current_send_action():
    should_block, reason = aa_mod._should_block_tool_call(
        tool_name='send_outreach_email',
        params={'body': 'Отправляю это сообщение партнёру'},
        user_rules=[],
        user_message='не отправляй эту информацию',
        coarse_blocked={},
    )

    assert should_block is True
    assert 'текущем запросе' in reason.lower()
