"""
Unit tests for security_monitor.py
"""
import pytest
import time
from security_monitor import SecurityMonitor


def test_security_monitor_initialization():
    """Test SecurityMonitor initialization"""
    monitor = SecurityMonitor()
    assert isinstance(monitor.blocked_ips, set)
    assert isinstance(monitor.rate_limits, dict)
    assert monitor.max_requests_per_minute == 60
    assert monitor.block_duration == 3600


def test_is_blocked():
    """Test IP blocking check"""
    monitor = SecurityMonitor()

    # Initially no IPs blocked
    assert not monitor.is_blocked("192.168.1.1")

    # Block an IP
    monitor.blocked_ips.add("192.168.1.1")
    assert monitor.is_blocked("192.168.1.1")


def test_rate_limiting():
    """Test rate limiting functionality"""
    monitor = SecurityMonitor()
    ip = "192.168.1.1"

    # First requests should be allowed
    for i in range(10):
        is_limited, reason = monitor.is_rate_limited(ip)
        assert not is_limited
        assert reason is None

    # Check that timestamps are recorded
    assert len(monitor.rate_limits[ip]) == 10


def test_rate_limit_exceeded():
    """Test when rate limit is exceeded"""
    monitor = SecurityMonitor()
    ip = "192.168.1.1"

    # Simulate many requests quickly
    for i in range(65):  # More than max_requests_per_minute
        monitor.is_rate_limited(ip)

    # Next request should be blocked
    is_limited, reason = monitor.is_rate_limited(ip)
    assert is_limited
    assert reason == "Too many requests"
    assert ip in monitor.blocked_ips


def test_unblock_ip():
    """Test manual IP unblocking"""
    monitor = SecurityMonitor()
    ip = "192.168.1.1"

    # Block IP
    monitor.blocked_ips.add(ip)
    assert monitor.is_blocked(ip)

    # Unblock IP
    monitor.unblock_ip(ip)
    assert not monitor.is_blocked(ip)


def test_rate_limit_cleanup():
    """Test that old timestamps are cleaned up"""
    monitor = SecurityMonitor()
    ip = "192.168.1.1"

    # Add old timestamp (more than 1 minute ago)
    old_time = time.time() - 120  # 2 minutes ago
    monitor.rate_limits[ip] = [old_time]

    # Make a new request
    is_limited, reason = monitor.is_rate_limited(ip)

    # Old timestamp should be cleaned up
    assert len(monitor.rate_limits[ip]) == 1
    assert monitor.rate_limits[ip][0] > old_time