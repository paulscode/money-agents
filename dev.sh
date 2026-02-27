#!/bin/bash

# Money Agents Development Server Manager
# All services run in Docker containers for consistency

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Start services
start_services() {
    log_info "Starting Money Agents services in Docker..."
    
    # Check if Docker is available
    if ! command -v docker &> /dev/null; then
        log_error "Docker not found. Please install Docker."
        exit 1
    fi
    
    cd "$PROJECT_ROOT"
    
    # Start all services with Docker Compose
    log_info "Starting all services (PostgreSQL, Redis, Backend, Frontend)..."
    docker compose up -d
    
    # Wait for services to be ready
    log_info "Waiting for services to be ready..."
    sleep 5
    
    log_info "✅ All services started!"
    log_info "Backend:  http://localhost:8000"
    log_info "Frontend: http://localhost:5173"
    log_info "API Docs: http://localhost:8000/docs"
}

# Stop services
stop_services() {
    log_info "Stopping Money Agents services..."
    
    cd "$PROJECT_ROOT"
    docker compose stop
    
    log_info "✅ All services stopped!"
}

# Restart services
restart_services() {
    log_info "Restarting Money Agents services..."
    
    cd "$PROJECT_ROOT"
    docker compose restart
    
    # Wait for services to be ready
    log_info "Waiting for services to be ready..."
    sleep 5
    
    log_info "✅ All services restarted!"
}

# Show status
show_status() {
    log_info "Money Agents Service Status:"
    echo ""
    
    cd "$PROJECT_ROOT"
    docker compose ps
}

# Show logs
show_logs() {
    local service="$1"
    cd "$PROJECT_ROOT"
    
    case "$service" in
        backend)
            docker compose logs -f backend
            ;;
        frontend)
            docker compose logs -f frontend
            ;;
        postgres)
            docker compose logs -f postgres
            ;;
        redis)
            docker compose logs -f redis
            ;;
        all|"")
            docker compose logs -f
            ;;
        *)
            log_error "Unknown service: $service"
            log_info "Available services: backend, frontend, postgres, redis, all"
            exit 1
            ;;
    esac
}

# Execute command in container
exec_cmd() {
    local service="$1"
    shift
    cd "$PROJECT_ROOT"
    
    case "$service" in
        backend)
            docker compose exec backend "$@"
            ;;
        frontend)
            docker compose exec frontend "$@"
            ;;
        *)
            log_error "Unknown service: $service"
            log_info "Available services: backend, frontend"
            exit 1
            ;;
    esac
}

# Main command handler
case "$1" in
    start)
        start_services
        ;;
    stop)
        stop_services
        ;;
    restart)
        restart_services
        ;;
    status)
        show_status
        ;;
    logs)
        show_logs "$2"
        ;;
    exec)
        exec_cmd "$2" "${@:3}"
        ;;
    *)
        echo "Money Agents Development Server Manager"
        echo "All services run in Docker containers"
        echo ""
        echo "Usage: $0 {start|stop|restart|status|logs|exec}"
        echo ""
        echo "Commands:"
        echo "  start           - Start all services (PostgreSQL, Redis, Backend, Frontend)"
        echo "  stop            - Stop all services"
        echo "  restart         - Restart all services"
        echo "  status          - Show status of all services"
        echo "  logs [service]  - Show logs (backend|frontend|postgres|redis|all)"
        echo "  exec service cmd - Execute command in service container"
        echo ""
        echo "Examples:"
        echo "  $0 start"
        echo "  $0 logs backend"
        echo "  $0 exec backend python reset_password.py user@example.com"
        echo ""
        exit 1
        ;;
esac
