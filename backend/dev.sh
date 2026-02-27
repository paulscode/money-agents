#!/bin/bash

# Development helper script for Money Agents backend

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}Money Agents - Backend Development Helper${NC}\n"

# Function to check if virtual environment is activated
check_venv() {
    if [ -z "$VIRTUAL_ENV" ]; then
        echo -e "${YELLOW}Warning: Virtual environment not activated${NC}"
        echo "Run: source venv/bin/activate"
        exit 1
    fi
}

# Parse command
case "$1" in
    "install")
        echo -e "${GREEN}Installing dependencies...${NC}"
        pip install --upgrade pip
        pip install -r requirements.txt
        ;;
    
    "migrate")
        check_venv
        echo -e "${GREEN}Creating migration...${NC}"
        alembic revision --autogenerate -m "${2:-Auto migration}"
        ;;
    
    "upgrade")
        check_venv
        echo -e "${GREEN}Applying migrations...${NC}"
        alembic upgrade head
        ;;
    
    "downgrade")
        check_venv
        echo -e "${GREEN}Rolling back migration...${NC}"
        alembic downgrade -1
        ;;
    
    "run")
        check_venv
        echo -e "${GREEN}Starting development server...${NC}"
        python -m app.main
        ;;
    
    "test")
        check_venv
        echo -e "${GREEN}Running tests...${NC}"
        pytest tests/ -v
        ;;
    
    "test-cov")
        check_venv
        echo -e "${GREEN}Running tests with coverage...${NC}"
        pytest tests/ -v --cov=app --cov-report=html
        ;;
    
    "format")
        check_venv
        echo -e "${GREEN}Formatting code...${NC}"
        black app/ tests/
        ruff check app/ tests/ --fix
        ;;
    
    "lint")
        check_venv
        echo -e "${GREEN}Linting code...${NC}"
        ruff check app/ tests/
        mypy app/
        ;;
    
    "shell")
        check_venv
        echo -e "${GREEN}Starting Python shell with app context...${NC}"
        python -i -c "from app.core.database import *; from app.models import *; print('Database and models imported')"
        ;;
    
    *)
        echo "Usage: ./dev.sh [command]"
        echo ""
        echo "Commands:"
        echo "  install     - Install Python dependencies"
        echo "  migrate     - Create new migration (optional: message)"
        echo "  upgrade     - Apply all pending migrations"
        echo "  downgrade   - Rollback last migration"
        echo "  run         - Start development server"
        echo "  test        - Run tests"
        echo "  test-cov    - Run tests with coverage report"
        echo "  format      - Format code with black and ruff"
        echo "  lint        - Run linters (ruff, mypy)"
        echo "  shell       - Open Python shell with app context"
        ;;
esac
