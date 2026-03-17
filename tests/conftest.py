"""Pytest configuration and shared fixtures."""
import sys
import os
import pytest

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Patch env before any project import
os.environ.setdefault("LOCAL", "1")
os.environ.setdefault("TELEGRAM_TOKEN", "test:token")
os.environ.setdefault("FREE_ACCESS_MODE", "1")

# test_full_suite.py is a standalone script (runs code at module level + sys.exit),
# not a pytest-compatible file — exclude from collection.
collect_ignore = ["test_full_suite.py"]
