#!/usr/bin/env python3
"""
Money Agents Development Server Manager (Cross-Platform)

A Python replacement for dev.sh that works on Windows, macOS, and Linux.
All services run in Docker containers for consistency.

Usage:
    python dev.py start           Start all services
    python dev.py stop            Stop all services
    python dev.py restart         Restart all services
    python dev.py status          Show status of all services
    python dev.py logs [service]  Show logs (backend|frontend|postgres|redis|all)
    python dev.py exec service cmd  Execute command in container

Examples:
    python dev.py start
    python dev.py logs backend
    python dev.py exec backend python reset_password.py user@example.com
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

# ANSI colors (work on modern Windows Terminal, macOS Terminal, Linux)
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
RED = '\033[0;31m'
NC = '\033[0m'  # No Color


def log_info(msg: str):
    print(f"{GREEN}[INFO]{NC} {msg}")


def log_warn(msg: str):
    print(f"{YELLOW}[WARN]{NC} {msg}")


def log_error(msg: str):
    print(f"{RED}[ERROR]{NC} {msg}")


def docker_compose(*args: str):
    """Run a docker compose command."""
    subprocess.run(["docker", "compose"] + list(args), cwd=PROJECT_ROOT)


def start_services():
    """Start all Docker services."""
    log_info("Starting Money Agents services in Docker...")

    # Check if Docker is available
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        if result.returncode != 0:
            log_error("Docker not found or not running. Please install/start Docker.")
            sys.exit(1)
    except Exception:
        log_error("Docker not found. Please install Docker.")
        sys.exit(1)

    log_info("Starting all services (PostgreSQL, Redis, Backend, Frontend)...")
    docker_compose("up", "-d")

    import time
    log_info("Waiting for services to be ready...")
    time.sleep(5)

    log_info("✅ All services started!")
    log_info("Backend:  http://localhost:8000")
    log_info("Frontend: http://localhost:5173")
    log_info("API Docs: http://localhost:8000/docs")


def stop_services():
    """Stop all Docker services."""
    log_info("Stopping Money Agents services...")
    docker_compose("stop")
    log_info("✅ All services stopped!")


def restart_services():
    """Restart all Docker services."""
    log_info("Restarting Money Agents services...")
    docker_compose("restart")

    import time
    log_info("Waiting for services to be ready...")
    time.sleep(5)

    log_info("✅ All services restarted!")


def show_status():
    """Show status of all services."""
    log_info("Money Agents Service Status:")
    print()
    docker_compose("ps")


def show_logs(service: str = ""):
    """Show logs for a service or all services."""
    valid_services = {"backend", "frontend", "postgres", "redis", "celery-worker", "celery-beat", "flower", "all", ""}

    if service and service not in valid_services:
        log_error(f"Unknown service: {service}")
        log_info(f"Available services: {', '.join(sorted(valid_services - {'', 'all'}))}")
        sys.exit(1)

    if service and service != "all":
        docker_compose("logs", "-f", service)
    else:
        docker_compose("logs", "-f")


def exec_cmd(service: str, *args: str):
    """Execute a command in a service container."""
    valid_services = {"backend", "frontend"}

    if service not in valid_services:
        log_error(f"Unknown service: {service}")
        log_info(f"Available services: {', '.join(sorted(valid_services))}")
        sys.exit(1)

    docker_compose("exec", service, *args)


def print_usage():
    """Print usage information."""
    print("""Money Agents Development Server Manager (Cross-Platform)
All services run in Docker containers.

Usage: python dev.py {start|stop|restart|status|logs|exec}

Commands:
  start              Start all services (PostgreSQL, Redis, Backend, Frontend)
  stop               Stop all services
  restart            Restart all services
  status             Show status of all services
  logs [service]     Show logs (backend|frontend|postgres|redis|all)
  exec service cmd   Execute command in service container

Examples:
  python dev.py start
  python dev.py logs backend
  python dev.py exec backend python reset_password.py user@example.com
""")


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == "start":
        start_services()
    elif command == "stop":
        stop_services()
    elif command == "restart":
        restart_services()
    elif command == "status":
        show_status()
    elif command == "logs":
        service = sys.argv[2] if len(sys.argv) > 2 else ""
        show_logs(service)
    elif command == "exec":
        if len(sys.argv) < 4:
            log_error("Usage: python dev.py exec <service> <command> [args...]")
            sys.exit(1)
        exec_cmd(sys.argv[2], *sys.argv[3:])
    else:
        print_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
