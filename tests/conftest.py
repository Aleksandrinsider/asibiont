"""Pytest configuration and shared fixtures."""
import sys
import os
import pytest

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Patch env before any project import
os.environ["LOCAL"] = "1"
os.environ.setdefault("TELEGRAM_TOKEN", "test:token")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test")
os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("FREE_ACCESS_MODE", "1")

# Force-import config early so LOCAL=1 is baked into the cached module
import config  # noqa: F401

# test_full_suite.py and test_question_vs_action.py are standalone scripts
# that require production DB — exclude from pytest collection.
collect_ignore = ["test_full_suite.py", "test_question_vs_action.py"]
