"""
Security monitoring module for blocking IPs and rate limiting
"""

import time
from collections import defaultdict

class SecurityMonitor:
    def __init__(self):
        self.blocked_ips = set()
        self.rate_limits = defaultdict(list)  # IP -> list of timestamps
        self.max_requests_per_minute = 60
        self.block_duration = 3600  # 1 hour in seconds

    def is_blocked(self, ip):
        """Check if IP is blocked"""
        return ip in self.blocked_ips

    def is_rate_limited(self, ip):
        """Check if IP exceeds rate limit"""
        now = time.time()
        # Clean old timestamps (older than 1 minute)
        self.rate_limits[ip] = [t for t in self.rate_limits[ip] if now - t < 60]

        if len(self.rate_limits[ip]) >= self.max_requests_per_minute:
            # Block the IP
            self.blocked_ips.add(ip)
            return True, "Too many requests"

        # Add current timestamp
        self.rate_limits[ip].append(now)
        return False, None

    def unblock_ip(self, ip):
        """Manually unblock an IP"""
        self.blocked_ips.discard(ip)

# Global instance
security_monitor = SecurityMonitor()