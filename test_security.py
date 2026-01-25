"""Test security monitoring system"""
import sys
import time
from datetime import datetime
from security_monitor import security_monitor, RequestLog

def test_basic_logging():
    """Test basic request logging"""
    print("Test 1: Basic logging...")
    
    security_monitor.log_request(
        ip="192.168.1.1",
        user_agent="Mozilla/5.0",
        path="/dashboard",
        method="GET",
        user_id=1
    )
    
    assert len(security_monitor.request_logs) > 0, "Should have logged requests"
    print("✅ Basic logging works")

def test_rate_limiting():
    """Test rate limiting per second"""
    print("\nTest 2: Rate limiting (per second)...")
    
    test_ip = "192.168.1.100"
    
    # Clear any existing data for this IP
    security_monitor.requests_per_ip[test_ip] = []
    
    # Send 10 requests (should be OK)
    for i in range(10):
        security_monitor.log_request(
            ip=test_ip,
            user_agent="Test Agent",
            path=f"/test{i}",
            method="GET"
        )
    
    is_limited, reason = security_monitor.is_rate_limited(test_ip)
    assert not is_limited, "10 requests should not be limited"
    print(f"  10 requests: OK (not limited)")
    
    # 11th request should trigger rate limit
    security_monitor.log_request(
        ip=test_ip,
        user_agent="Test Agent",
        path="/test11",
        method="GET"
    )
    
    is_limited, reason = security_monitor.is_rate_limited(test_ip)
    assert is_limited, "11 requests per second should be limited"
    print(f"  11 requests: ✅ Rate limited - {reason}")

def test_blocking():
    """Test IP blocking after violations"""
    print("\nTest 3: IP blocking after violations...")
    
    test_ip = "192.168.1.200"
    
    # Clear any existing data
    security_monitor.requests_per_ip[test_ip] = []
    security_monitor.suspicious_ips[test_ip] = 0
    security_monitor.blocked_ips.pop(test_ip, None)
    
    # Simulate 5 violations
    for i in range(5):
        security_monitor.suspicious_ips[test_ip] = i + 1
        
        if i < 4:
            assert not security_monitor.is_blocked(test_ip), f"Should not be blocked at {i+1} violations"
            print(f"  {i+1} violations: OK (not blocked yet)")
    
    # Block on 5th violation
    security_monitor.block_ip(test_ip)
    assert security_monitor.is_blocked(test_ip), "Should be blocked after 5 violations"
    print(f"  5 violations: ✅ IP blocked")

def test_statistics():
    """Test statistics gathering"""
    print("\nTest 4: Statistics gathering...")
    
    # Add some test data
    test_ips = ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
    test_paths = ["/dashboard", "/api/tasks", "/api/partners"]
    
    for ip in test_ips:
        for path in test_paths:
            security_monitor.log_request(
                ip=ip,
                user_agent="Test Agent",
                path=path,
                method="GET"
            )
    
    stats = security_monitor.get_statistics()
    
    assert stats['total_requests'] > 0, "Should have total requests"
    assert stats['unique_ips'] >= len(test_ips), "Should have unique IPs"
    assert len(stats['top_ips']) > 0, "Should have top IPs"
    assert len(stats['top_endpoints']) > 0, "Should have top endpoints"
    
    print(f"  Total requests: {stats['total_requests']}")
    print(f"  Unique IPs: {stats['unique_ips']}")
    print(f"  Top IPs count: {len(stats['top_ips'])}")
    print(f"  Top endpoints count: {len(stats['top_endpoints'])}")
    print("✅ Statistics working")

def test_ip_info():
    """Test getting IP information"""
    print("\nTest 5: IP information...")
    
    test_ip = "10.0.0.5"
    security_monitor.requests_per_ip[test_ip] = []
    
    # Add some requests
    for i in range(3):
        security_monitor.log_request(
            ip=test_ip,
            user_agent="Test Agent",
            path=f"/test{i}",
            method="GET",
            user_id=123
        )
    
    ip_info = security_monitor.get_ip_info(test_ip)
    
    assert ip_info['ip'] == test_ip, "Should return correct IP"
    assert ip_info['total_requests'] >= 3, "Should have request count"
    assert len(ip_info['recent_requests']) > 0, "Should have recent requests"
    
    print(f"  IP: {ip_info['ip']}")
    print(f"  Total requests: {ip_info['total_requests']}")
    print(f"  Recent requests: {len(ip_info['recent_requests'])}")
    print("✅ IP info working")

def test_cleanup():
    """Test cleanup of old data"""
    print("\nTest 6: Cleanup old data...")
    
    initial_count = len(security_monitor.request_logs)
    security_monitor.cleanup_old_data()
    
    # In a real test with old data, this would reduce the count
    # For now, just verify it runs without error
    print(f"  Initial logs: {initial_count}")
    print(f"  After cleanup: {len(security_monitor.request_logs)}")
    print("✅ Cleanup runs without error")

def main():
    print("=" * 60)
    print("Security Monitor Test Suite")
    print("=" * 60)
    
    try:
        test_basic_logging()
        test_rate_limiting()
        test_blocking()
        test_statistics()
        test_ip_info()
        test_cleanup()
        
        print("\n" + "=" * 60)
        print("✅ All tests passed!")
        print("=" * 60)
        
        # Show final statistics
        print("\nFinal Statistics:")
        stats = security_monitor.get_statistics()
        print(f"  Total requests: {stats['total_requests']}")
        print(f"  Unique IPs: {stats['unique_ips']}")
        print(f"  Blocked IPs: {len(stats['blocked_ips'])}")
        print(f"  Suspicious IPs: {len(stats['suspicious_ips'])}")
        
        return 0
        
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
