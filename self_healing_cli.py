#!/usr/bin/env python3
"""
Утилита управления системой самоисправления AI агента
"""
import argparse
import sys
import os
import time
from datetime import datetime

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.self_healing import get_self_healing_agent
from ai_integration.logging import get_async_agent_logger


def print_header():
    """Вывод заголовка"""
    print("🤖 AI Agent Self-Healing System Control")
    print("=" * 50)


def print_status(agent):
    """Вывод статуса системы"""
    print("\n📊 System Status:")
    print(f"  Monitoring active: {'✅' if agent.is_monitoring else '❌'}")
    print(f"  Version history: {len(agent.version_history)} snapshots")
    print(f"  Consecutive failures: {agent.consecutive_failures}")
    print(f"  Last health check: {agent.last_health_check}")

    if agent.version_history:
        latest = agent.version_history[-1]
        print(f"  Latest snapshot: {latest.code_version} ({'✅ working' if latest.is_working else '❌ broken'})")


def print_health_metrics(agent):
    """Вывод метрик здоровья"""
    print("\n🏥 Health Metrics:")
    try:
        metrics = agent._get_health_metrics()
        for key, value in metrics.items():
            if isinstance(value, float):
                print(f"  {key}: {value:.1f}")
            else:
                print(f"  {key}: {value}")
    except Exception as e:
        print(f"  Error getting metrics: {e}")


def print_version_history(agent, limit=5):
    """Вывод истории версий"""
    print(f"\n📚 Version History (last {limit}):")
    if not agent.version_history:
        print("  No snapshots found")
        return

    for snapshot in agent.version_history[-limit:]:
        status = "✅" if snapshot.is_working else "❌"
        print(f"  {status} {snapshot.timestamp.strftime('%Y-%m-%d %H:%M:%S')} - {snapshot.code_version}")


def cmd_status(args):
    """Команда статуса"""
    agent = get_self_healing_agent()
    print_header()
    print_status(agent)
    print_health_metrics(agent)
    print_version_history(agent)


def cmd_start(args):
    """Запуск мониторинга"""
    agent = get_self_healing_agent()
    if agent.is_monitoring:
        print("❌ Monitoring is already running")
        return

    agent.start_monitoring()
    print("✅ Monitoring started")

    if args.daemon:
        print("🔄 Running in daemon mode (press Ctrl+C to stop)")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n🛑 Stopping monitoring...")
            agent.stop_monitoring()
            print("✅ Monitoring stopped")


def cmd_stop(args):
    """Остановка мониторинга"""
    agent = get_self_healing_agent()
    if not agent.is_monitoring:
        print("❌ Monitoring is not running")
        return

    agent.stop_monitoring()
    print("✅ Monitoring stopped")


def cmd_snapshot(args):
    """Создание снимка системы"""
    agent = get_self_healing_agent()
    is_working = not args.broken

    agent.create_system_snapshot(is_working=is_working)
    status = "✅ working" if is_working else "❌ broken"
    print(f"📸 System snapshot created ({status})")


def cmd_rollback(args):
    """Откат системы"""
    agent = get_self_healing_agent()

    if args.type == "code":
        print("🔄 Rolling back code...")
        agent._rollback_code()
        print("✅ Code rolled back")
    elif args.type == "config":
        print("🔄 Rolling back config...")
        agent._rollback_config()
        print("✅ Config rolled back")
    elif args.type == "all":
        print("🔄 Rolling back code and config...")
        agent._rollback_code()
        agent._rollback_config()
        print("✅ Code and config rolled back")


def cmd_restart(args):
    """Перезапуск сервиса"""
    agent = get_self_healing_agent()
    print("🔄 Restarting service...")
    agent._restart_service()
    print("✅ Service restart initiated")


def cmd_clean(args):
    """Очистка старых бэкапов"""
    agent = get_self_healing_agent()
    print("🧹 Cleaning old backups...")
    agent._cleanup_old_backups()
    print("✅ Old backups cleaned")


def cmd_test(args):
    """Тестирование системы"""
    print("🧪 Testing Self-Healing System...")

    agent = get_self_healing_agent()
    logger = get_async_agent_logger()

    # Тест создания снимка
    print("  📸 Creating test snapshot...")
    agent.create_system_snapshot(is_working=True)

    # Тест логирования
    print("  📝 Testing async logging...")
    import asyncio
    asyncio.run(logger.log_interaction_async(
        user_id=99999,
        message_type='test',
        user_message='Test message',
        agent_response='Test response'
    ))

    # Тест метрик
    print("  📊 Testing metrics...")
    metrics = logger.get_performance_metrics(99999, days=1, use_cache=False)
    print(f"    Metrics: {metrics}")

    # Тест здоровья
    print("  🏥 Testing health check...")
    health = agent._evaluate_system_health(agent._get_health_metrics())
    print(f"    System healthy: {health}")

    print("✅ All tests completed!")


def main():
    """Главная функция"""
    parser = argparse.ArgumentParser(description="AI Agent Self-Healing System Control")
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Команда статуса
    status_parser = subparsers.add_parser('status', help='Show system status')
    status_parser.set_defaults(func=cmd_status)

    # Команда запуска
    start_parser = subparsers.add_parser('start', help='Start monitoring')
    start_parser.add_argument('--daemon', action='store_true', help='Run in daemon mode')
    start_parser.set_defaults(func=cmd_start)

    # Команда остановки
    stop_parser = subparsers.add_parser('stop', help='Stop monitoring')
    stop_parser.set_defaults(func=cmd_stop)

    # Команда снимка
    snapshot_parser = subparsers.add_parser('snapshot', help='Create system snapshot')
    snapshot_parser.add_argument('--broken', action='store_true', help='Mark snapshot as broken')
    snapshot_parser.set_defaults(func=cmd_snapshot)

    # Команда отката
    rollback_parser = subparsers.add_parser('rollback', help='Rollback system')
    rollback_parser.add_argument('type', choices=['code', 'config', 'all'], help='What to rollback')
    rollback_parser.set_defaults(func=cmd_rollback)

    # Команда перезапуска
    restart_parser = subparsers.add_parser('restart', help='Restart service')
    restart_parser.set_defaults(func=cmd_restart)

    # Команда очистки
    clean_parser = subparsers.add_parser('clean', help='Clean old backups')
    clean_parser.set_defaults(func=cmd_clean)

    # Команда тестирования
    test_parser = subparsers.add_parser('test', help='Run system tests')
    test_parser.set_defaults(func=cmd_test)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    try:
        args.func(args)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()