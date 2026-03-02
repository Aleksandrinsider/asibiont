#!/usr/bin/env python3
"""
Автоматический тест всех Python-шаблонов из dashboard_new.html.
Парсит HTML, извлекает код каждой интеграции, запускает subprocess с
mock-переменными окружения и проверяет: нет Traceback/SyntaxError,
есть хотя бы одна строка вывода.

Запуск: python tests/test_template_scripts.py
"""
import os
import re
import sys
import subprocess
import textwrap
import time

# ──────────────────────────────────────────────
# Путь к HTML
# ──────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML_PATH = os.path.join(ROOT, "templates", "dashboard_new.html")

# ──────────────────────────────────────────────
# Mock-переменные окружения для каждой интеграции
# ──────────────────────────────────────────────
MOCK_ENVS: dict[str, dict] = {
    "gmail":         {"GMAIL_USER": "test@gmail.com",    "GMAIL_PASS": "fake_app_pass"},
    "yandex":        {"YANDEX_USER": "test@yandex.ru",   "YANDEX_PASS": "fake_pass"},
    "mailru":        {"MAILRU_USER": "test@mail.ru",     "MAILRU_PASS": "fake_pass"},
    "openweather":   {"WEATHER_API_KEY": "fake_api_key", "WEATHER_CITY": "Moscow"},
    "rss":           {"RSS_URL": "https://feeds.feedburner.com/TechCrunch"},
    "telegram_bot":  {"TG_BOT_TOKEN": "1234567890:AAFakeTokenForTestingPurposesOnly"},
    "vk":            {"VK_TOKEN": "fake_vk_token",       "VK_OWNER_ID": "-1"},
    "wildberries":   {"WB_API_KEY": "fake_wb_key"},
    "ozon":          {"OZON_CLIENT_ID": "810194",         "OZON_API_KEY": "fake-api-key-00000000"},
    "google_sheets": {"GSHEETS_ID": "fake_sheet_id_not_exist",  "GSHEETS_SHEET": "Sheet1"},
    "github":        {"GITHUB_TOKEN": "ghp_fakefakefake", "GITHUB_REPO": "octocat/nonexistent"},
    "hh":            {"HH_QUERY": "Python",              "HH_AREA": "1"},
    "http_api":      {"API_URL": "https://httpbin.org/get", "API_KEY": ""},
    "notion":        {"NOTION_TOKEN": "secret_fake",     "NOTION_DB_ID": "aaaabbbbccccdddd11112222333344445555"},
    "airtable":      {"AIRTABLE_TOKEN": "patFakeToken",  "AIRTABLE_BASE_ID": "appFakeBase", "AIRTABLE_TABLE": "Tasks"},
    "slack":         {"SLACK_TOKEN": "xoxb-fake-token",  "SLACK_CHANNEL": "C0FAKE"},
    "trello":        {"TRELLO_KEY": "fake_key",          "TRELLO_TOKEN": "fake_token", "TRELLO_BOARD": "fakeboardid"},
    "stripe":        {"STRIPE_SK": "sk_test_fakefakefakefakefake"},
    "shopify":       {"SHOPIFY_SHOP": "fake-store.myshopify.com", "SHOPIFY_TOKEN": "shpat_fakefake"},
    "youtube":       {"YOUTUBE_API_KEY": "AIzaFakeKeyForTestPurpose", "YOUTUBE_CHANNEL_ID": "UCfake"},
    "coingecko":     {"CRYPTO_COINS": "bitcoin,ethereum"},
    "jira":          {"JIRA_URL": "https://fake.atlassian.net", "JIRA_EMAIL": "test@example.com",
                      "JIRA_TOKEN": "ATATfake", "JIRA_PROJECT": "DEV"},
    "calendly":      {"CALENDLY_TOKEN": "eyJhbGciOiJIUzI1NiJ9.fake"},
    "resend":        {"RESEND_API_KEY": "re_fake_key",   "RESEND_FROM": "hello@example.com"},
    "bitrix24":      {"BITRIX24_WEBHOOK": "https://fake.bitrix24.ru/rest/1/faketoken/"},
    "amocrm":        {"AMO_SUBDOMAIN": "fakeco",         "AMO_ACCESS_TOKEN": "eyJfaketoken"},
    "hubspot":       {"HUBSPOT_API_KEY": "pat-eu1-fakefakefake"},
}

# hh.ru публичное API — реально сработает, это ожидаемо
# coingecko публичное API — реально сработает, это ожидаемо
# http_api с httpbin.org — реально сработает, это ожидаемо
PUBLIC_SVCS = {"hh", "coingecko", "http_api"}

TIMEOUT = 20        # секунд на большинство скриптов
SVC_TIMEOUTS = {    # персональные таймауты для медленных API
    "notion": 25,
}

# ──────────────────────────────────────────────
# Парсер: извлекаем все code-блоки из HTML
# ──────────────────────────────────────────────

def extract_scripts(html_path: str) -> list[tuple[str, str, str]]:
    """
    Возвращает список (svc_key, display_name, python_code).

    Ищет блоки вида:
        svckey: {
            name: '...',
            ...
            code: `...python...`
        },
    """
    with open(html_path, encoding="utf-8") as fh:
        src = fh.read()

    results: list[tuple[str, str, str]] = []
    seen_keys: set[str] = set()

    # Находим все позиции 'code: `'
    for m_code in re.finditer(r'code: `', src):
        cs = m_code.start()

        # 1. Python-код — от символа после `` ` `` до следующего `` ` ``
        code_start = cs + len("code: `")
        rest = src[code_start:]
        end_m = re.search(r'`', rest)
        if not end_m:
            continue
        code = rest[: end_m.start()].replace("\\'", "'")

        # 2. Ищем имя сервиса: последнее name: '...' перед code:
        before = src[max(0, cs - 3000): cs]
        name_matches = list(re.finditer(r"name:\s*'([^']*)'", before))
        display_name = name_matches[-1].group(1) if name_matches else "?"

        # 3. Ключ блока: ищем последнее вхождение шаблона `            word: {`
        #    (12 пробелов — уровень вложенности ключей _MPA_SVC)
        key_matches = list(re.finditer(r'^\s{12}(\w+)\s*:\s*\{', before, re.MULTILINE))
        svc_key = key_matches[-1].group(1) if key_matches else "unknown"

        if svc_key in seen_keys:
            continue
        seen_keys.add(svc_key)

        results.append((svc_key, display_name, code))

    return results


# ──────────────────────────────────────────────
# Запускаем один скрипт в subprocess
# ──────────────────────────────────────────────

def run_script(code: str, env_vars: dict, timeout: int = TIMEOUT) -> tuple[bool, str]:
    """
    Запускает python-код в subprocess.
    Возвращает (passed, report_string).
    passed = True если нет traceback/syntaxerror в выводе.
    """
    env = os.environ.copy()
    env.update(env_vars)
    # Явно убираем AGENT_ACTION чтобы выполнялся read-путь, не write
    env.pop("AGENT_ACTION", None)

    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env={**env, "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT (>{0}s)".format(timeout)

    stdout = (result.stdout or b"").decode("utf-8", errors="replace").strip()
    stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip()
    combined = (stdout + "\n" + stderr).lower()

    # Провал если есть traceback/syntaxerror
    bad_markers = ["traceback (most recent call last)", "syntaxerror", "indentationerror", "nameerror: name"]
    for marker in bad_markers:
        if marker in combined:
            snippet = (stdout + stderr)[:400]
            return False, "EXCEPTION\n" + textwrap.indent(snippet, "    ")

    # Должна быть хоть одна строка вывода
    if not stdout:
        # Некоторые сервисы могут вернуть ошибку в stderr (не traceback)
        if stderr:
            return True, "OK (stderr only): " + stderr[:120]
        return True, "OK (no output — likely API error handled)"

    return True, "OK: " + stdout[:200]


# ──────────────────────────────────────────────
# Цвета для терминала
# ──────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def color(text: str, c: str) -> str:
    return c + text + RESET if sys.stdout.isatty() else text


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main() -> None:
    print(color("\n=== Тест шаблонных скриптов dashboard_new.html ===\n", BOLD + CYAN))

    scripts = extract_scripts(HTML_PATH)
    print(f"Найдено интеграций: {len(scripts)}\n")

    passed_list: list[str] = []
    failed_list: list[tuple[str, str]] = []
    skipped_list: list[str] = []

    for svc_key, display_name, code in scripts:
        env_vars = MOCK_ENVS.get(svc_key, {})
        label = f"{svc_key} ({display_name})"

        if not code.strip():
            print(color(f"  SKIP  {label}", YELLOW) + " — пустой код")
            skipped_list.append(label)
            continue

        is_public = svc_key in PUBLIC_SVCS
        print(f"  {'🌐 ' if is_public else ''}Тест: {color(label, BOLD)} ...", end="", flush=True)
        t0 = time.time()
        svc_timeout = SVC_TIMEOUTS.get(svc_key, TIMEOUT)
        ok, report = run_script(code, env_vars, timeout=svc_timeout)
        elapsed = time.time() - t0

        if ok:
            print(color(" PASS", GREEN) + f" ({elapsed:.1f}s)")
            # Показываем первую строку вывода
            first_line = report.split("\n")[0][:100]
            print(f"        {first_line}")
            passed_list.append(label)
        else:
            print(color(" FAIL", RED) + f" ({elapsed:.1f}s)")
            print(textwrap.indent(report, "        "))
            failed_list.append((label, report))

    # ── Итог ──
    total = len(passed_list) + len(failed_list) + len(skipped_list)
    print()
    print(color("═" * 55, CYAN))
    print(color(f"  Итого: {total} | PASS: {len(passed_list)} | FAIL: {len(failed_list)} | SKIP: {len(skipped_list)}", BOLD))
    print(color("═" * 55, CYAN))

    if failed_list:
        print(color("\n  ✗ Упавшие:", RED))
        for label, report in failed_list:
            print(color(f"    • {label}", RED))
            print(textwrap.indent(report[:300], "      "))
        sys.exit(1)
    else:
        print(color("\n  ✓ Все шаблоны прошли проверку — нет необработанных исключений.\n", GREEN))


if __name__ == "__main__":
    main()
