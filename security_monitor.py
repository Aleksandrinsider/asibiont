"""
Security monitoring module for blocking IPs and rate limiting
"""

import time
from collections import defaultdict
from datetime import datetime

class RequestLog:
    def __init__(self, ip, user_agent, path, method, user_id=None):
        self.timestamp = datetime.now()
        self.ip = ip
        self.user_agent = user_agent
        self.path = path
        self.method = method
        self.user_id = user_id
        self.status_code = None

class SecurityMonitor:
    def __init__(self):
        self.blocked_ips = set()
        self.rate_limits = defaultdict(list)  # IP -> list of timestamps
        self.max_requests_per_minute = 60
        self.block_duration = 3600  # 1 hour in seconds
        self.request_logs = []  # List of RequestLog objects
        self.max_logs = 1000  # Keep only last 1000 logs

    def is_blocked(self, ip):
        """Check if IP is blocked - DISABLED: always return False"""
        return False

    def is_rate_limited(self, ip):
        """Check if IP exceeds rate limit - DISABLED: always return False"""
        # Still track requests for monitoring, but don't block
        now = time.time()
        # Clean old timestamps (older than 1 minute)
        self.rate_limits[ip] = [t for t in self.rate_limits[ip] if now - t < 60]

        # Add current timestamp for monitoring
        self.rate_limits[ip].append(now)
        return False, None

    def log_request(self, ip, user_agent, path, method, user_id=None):
        """Log an incoming request"""
        log_entry = RequestLog(ip, user_agent, path, method, user_id)
        self.request_logs.append(log_entry)

        # Keep only last N logs
        if len(self.request_logs) > self.max_logs:
            self.request_logs = self.request_logs[-self.max_logs:]

    def unblock_ip(self, ip):
        """Manually unblock an IP"""
        self.blocked_ips.discard(ip)

    def get_recent_logs(self, limit=100):
        """Get recent request logs"""
        return self.request_logs[-limit:]

    def get_logs_by_ip(self, ip, limit=50):
        """Get logs for specific IP"""
        ip_logs = [log for log in self.request_logs if log.ip == ip]
        return ip_logs[-limit:]

# Global instance
security_monitor = SecurityMonitor()