#!/usr/bin/env bash
# ============================================================================
# Datetime Hygiene Check
# ============================================================================
# Prevents reintroduction of deprecated/naive datetime patterns.
# 
# Usage:
#   ./scripts/check-datetime-hygiene.sh          # check full codebase
#   ./scripts/check-datetime-hygiene.sh --staged  # check staged files only (git hook)
#
# Install as pre-commit hook:
#   ln -sf ../../scripts/check-datetime-hygiene.sh .git/hooks/pre-commit
# ============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ERRORS=0

if [[ "${1:-}" == "--staged" ]]; then
    # Only check staged Python files in backend/app/
    FILES=$(git diff --cached --name-only --diff-filter=ACM -- 'backend/app/*.py' 2>/dev/null || true)
    if [[ -z "$FILES" ]]; then
        exit 0
    fi
    MODE="staged"
else
    # Check all Python files in backend/app/
    FILES=$(find backend/app -name '*.py' -not -path '*/__pycache__/*' -not -name 'datetime_utils.py')
    MODE="full"
fi

# --- Check 1: datetime.utcnow (deprecated in Python 3.12, produces naive datetimes) ---
MATCHES=$(echo "$FILES" | xargs grep -ln 'datetime\.utcnow' 2>/dev/null || true)
if [[ -n "$MATCHES" ]]; then
    echo -e "${RED}ERROR: datetime.utcnow() found (deprecated, produces naive datetimes)${NC}"
    echo -e "${YELLOW}Use:  from app.core.datetime_utils import utc_now${NC}"
    echo "$MATCHES" | while read -r f; do
        grep -n 'datetime\.utcnow' "$f" | head -5
    done
    ERRORS=$((ERRORS + 1))
fi

# --- Check 2: .replace(tzinfo=None) — stripping timezone info ---
MATCHES=$(echo "$FILES" | xargs grep -ln '\.replace(tzinfo=None)' 2>/dev/null || true)
if [[ -n "$MATCHES" ]]; then
    echo -e "${RED}ERROR: .replace(tzinfo=None) found (stripping timezone is unsafe)${NC}"
    echo -e "${YELLOW}Use:  from app.core.datetime_utils import ensure_utc${NC}"
    echo "$MATCHES" | while read -r f; do
        grep -n '\.replace(tzinfo=None)' "$f" | head -5
    done
    ERRORS=$((ERRORS + 1))
fi

# --- Check 3: .replace(tzinfo=timezone.utc) — use ensure_utc() instead ---
MATCHES=$(echo "$FILES" | xargs grep -ln '\.replace(tzinfo=timezone\.utc)' 2>/dev/null | grep -v 'datetime_utils.py' || true)
if [[ -n "$MATCHES" ]]; then
    echo -e "${RED}ERROR: .replace(tzinfo=timezone.utc) found (use ensure_utc() instead)${NC}"
    echo -e "${YELLOW}Use:  from app.core.datetime_utils import ensure_utc${NC}"
    echo "$MATCHES" | while read -r f; do
        grep -n '\.replace(tzinfo=timezone\.utc)' "$f" | head -5
    done
    ERRORS=$((ERRORS + 1))
fi

# --- Summary ---
if [[ $ERRORS -gt 0 ]]; then
    echo ""
    echo -e "${RED}Datetime hygiene check failed ($ERRORS issue(s) found)${NC}"
    echo -e "See: internal_docs/DATETIME_GETWELL_PLAN.md"
    exit 1
else
    if [[ "$MODE" == "full" ]]; then
        echo -e "${GREEN}Datetime hygiene check passed ✓${NC}"
    fi
    exit 0
fi
