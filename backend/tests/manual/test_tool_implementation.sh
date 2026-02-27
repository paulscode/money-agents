#!/bin/bash
#
# Tool Implementation Test Script
# 
# This script tests the full workflow of implementing and using tools:
# 1. Create tool records in "implementing" status
# 2. Configure interface settings
# 3. Move to "implemented" status
# 4. Test execution via API
# 5. Test resource queue integration (for GPU tool)
#
# ===== ARCHITECTURE =====
#
# The mock services run on the HOST machine (not in Docker).
# This simulates real-world custom tools like Ollama, ffmpeg, etc.
#
#   ┌─────────────────────────────────────────────────────────┐
#   │  HOST MACHINE                                            │
#   │                                                          │
#   │  ┌──────────────────┐   ┌──────────────────────┐        │
#   │  │ mock_gpu_api.py  │   │ mock_cli_api.py      │        │
#   │  │ Port: 9999       │   │ Port: 9998           │        │
#   │  │ (GPU Service)    │   │ (wraps CLI tool)     │        │
#   │  └────────▲─────────┘   └──────────▲───────────┘        │
#   │           │                        │                     │
#   │           │ host.docker.internal   │                     │
#   │           │                        │                     │
#   │  ┌────────┴────────────────────────┴───────────┐        │
#   │  │            DOCKER CONTAINERS                 │        │
#   │  │                                              │        │
#   │  │   Backend (agents) ──► ToolExecutor         │        │
#   │  │                        │                     │        │
#   │  │   Calls: http://host.docker.internal:9999   │        │
#   │  │   Calls: http://host.docker.internal:9998   │        │
#   │  └──────────────────────────────────────────────┘        │
#   └─────────────────────────────────────────────────────────┘
#
# Prerequisites:
# - Backend running (docker compose up -d)
# - docker-compose.yml has: extra_hosts: ["host.docker.internal:host-gateway"]
# - Mock GPU API running on HOST: python backend/tests/manual/mock_gpu_api.py
# - Mock CLI API running on HOST: python backend/tests/manual/mock_cli_api.py
# - User authenticated

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo_step() { echo -e "\n${BLUE}▶ $1${NC}"; }
echo_success() { echo -e "${GREEN}✓ $1${NC}"; }
echo_error() { echo -e "${RED}✗ $1${NC}"; }
echo_warn() { echo -e "${YELLOW}⚠ $1${NC}"; }

# Configuration
API_BASE="http://localhost:8000/api/v1"
MOCK_GPU_HOST="host.docker.internal"
MOCK_GPU_PORT="9999"

# Get auth token
get_token() {
    echo_step "Authenticating..."
    
    # Try to use existing token
    if [[ -f /tmp/test_vars.sh ]]; then
        source /tmp/test_vars.sh
        if [[ -n "$TOKEN" ]]; then
            # Verify token still works
            if curl -sf "$API_BASE/users/me" -H "Authorization: Bearer $TOKEN" > /dev/null 2>&1; then
                echo_success "Using existing token"
                return 0
            fi
        fi
    fi
    
    # Get new token
    echo "Enter credentials:"
    read -p "Email or Username: " IDENTIFIER
    read -sp "Password: " PASSWORD
    echo
    
    RESPONSE=$(curl -sf "$API_BASE/auth/login" \
        -H "Content-Type: application/json" \
        -d "{\"identifier\": \"$IDENTIFIER\", \"password\": \"$PASSWORD\"}")
    
    TOKEN=$(echo "$RESPONSE" | jq -r '.access_token')
    
    if [[ "$TOKEN" == "null" || -z "$TOKEN" ]]; then
        echo_error "Login failed"
        exit 1
    fi
    
    echo "export TOKEN=$TOKEN" > /tmp/test_vars.sh
    echo_success "Authenticated"
}

# Test mock GPU API is running
test_mock_gpu_api() {
    echo_step "Testing Mock GPU API..."
    
    if curl -sf "http://localhost:$MOCK_GPU_PORT/health" > /dev/null 2>&1; then
        echo_success "Mock GPU API is running on port $MOCK_GPU_PORT"
    else
        echo_error "Mock GPU API not running!"
        echo "Start it with: python backend/tests/manual/mock_gpu_api.py"
        exit 1
    fi
    
    # Show GPU status
    echo "GPU Status:"
    curl -s "http://localhost:$MOCK_GPU_PORT/gpu/status" | jq '.'
}

# Create test tool records
create_tool_records() {
    echo_step "Creating test tool records..."
    
    # Tool 1: Mock GPU Image Generator
    echo "Creating 'Mock GPU Image Generator' tool..."
    GPU_TOOL_RESPONSE=$(curl -s "$API_BASE/tools" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d '{
            "name": "Mock GPU Image Generator",
            "slug": "mock-gpu-imgen",
            "category": "api",
            "description": "Test tool that simulates GPU-intensive image generation. Requires GPU resource and connects to mock API on host.",
            "tags": ["test", "gpu", "image-generation"],
            "status": "implementing",
            "implementation_notes": "Needs interface configuration for mock GPU API",
            "usage_instructions": "Submit prompts to generate fake images. Uses mock GPU API on host.",
            "integration_complexity": "medium",
            "cost_model": "free",
            "priority": "high"
        }')
    
    GPU_TOOL_ID=$(echo "$GPU_TOOL_RESPONSE" | jq -r '.id')
    
    if [[ "$GPU_TOOL_ID" != "null" && -n "$GPU_TOOL_ID" ]]; then
        echo_success "Created GPU tool: $GPU_TOOL_ID"
        echo "export GPU_TOOL_ID=$GPU_TOOL_ID" >> /tmp/test_vars.sh
    else
        # Tool might already exist
        GPU_TOOL_RESPONSE=$(curl -s "$API_BASE/tools?search=mock-gpu-imgen" \
            -H "Authorization: Bearer $TOKEN")
        GPU_TOOL_ID=$(echo "$GPU_TOOL_RESPONSE" | jq -r '.items[0].id // empty')
        
        if [[ -n "$GPU_TOOL_ID" ]]; then
            echo_warn "GPU tool already exists: $GPU_TOOL_ID"
            echo "export GPU_TOOL_ID=$GPU_TOOL_ID" >> /tmp/test_vars.sh
        else
            echo_error "Failed to create GPU tool"
            echo "$GPU_TOOL_RESPONSE" | jq '.'
        fi
    fi
    
    # Tool 2: Mock CLI Analyzer
    echo "Creating 'Mock CLI Analyzer' tool..."
    CLI_TOOL_RESPONSE=$(curl -s "$API_BASE/tools" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d '{
            "name": "Mock CLI Analyzer",
            "slug": "mock-cli-analyzer",
            "category": "analysis",
            "description": "Test tool that runs a CLI command to analyze text. Demonstrates command-line tool integration.",
            "tags": ["test", "cli", "text-analysis"],
            "status": "implementing",
            "implementation_notes": "Needs CLI executor configuration",
            "usage_instructions": "Submit text to analyze. Returns word count, character stats, etc.",
            "integration_complexity": "low",
            "cost_model": "free",
            "priority": "medium"
        }')
    
    CLI_TOOL_ID=$(echo "$CLI_TOOL_RESPONSE" | jq -r '.id')
    
    if [[ "$CLI_TOOL_ID" != "null" && -n "$CLI_TOOL_ID" ]]; then
        echo_success "Created CLI tool: $CLI_TOOL_ID"
        echo "export CLI_TOOL_ID=$CLI_TOOL_ID" >> /tmp/test_vars.sh
    else
        # Tool might already exist
        CLI_TOOL_RESPONSE=$(curl -s "$API_BASE/tools?search=mock-cli-analyzer" \
            -H "Authorization: Bearer $TOKEN")
        CLI_TOOL_ID=$(echo "$CLI_TOOL_RESPONSE" | jq -r '.items[0].id // empty')
        
        if [[ -n "$CLI_TOOL_ID" ]]; then
            echo_warn "CLI tool already exists: $CLI_TOOL_ID"
            echo "export CLI_TOOL_ID=$CLI_TOOL_ID" >> /tmp/test_vars.sh
        else
            echo_error "Failed to create CLI tool"
            echo "$CLI_TOOL_RESPONSE" | jq '.'
        fi
    fi
}

# Check what resources exist
check_resources() {
    echo_step "Checking available resources..."
    
    RESOURCES=$(curl -s "$API_BASE/resources" -H "Authorization: Bearer $TOKEN")
    echo "Available resources:"
    echo "$RESOURCES" | jq '.items[] | {id, name, type, is_enabled}'
    
    # Look for a GPU resource
    GPU_RESOURCE_ID=$(echo "$RESOURCES" | jq -r '.items[] | select(.type == "gpu") | .id' | head -1)
    
    if [[ -n "$GPU_RESOURCE_ID" && "$GPU_RESOURCE_ID" != "null" ]]; then
        echo_success "Found GPU resource: $GPU_RESOURCE_ID"
        echo "export GPU_RESOURCE_ID=$GPU_RESOURCE_ID" >> /tmp/test_vars.sh
    else
        echo_warn "No GPU resource found. Creating one..."
        
        # Create a GPU resource for testing
        RESOURCE_RESPONSE=$(curl -s "$API_BASE/resources" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -d '{
                "name": "Test GPU (Mock)",
                "type": "gpu",
                "description": "Mock GPU resource for testing tool execution",
                "is_enabled": true,
                "metadata": {
                    "model": "Mock RTX 3090",
                    "vram_gb": 24,
                    "mock": true
                }
            }')
        
        GPU_RESOURCE_ID=$(echo "$RESOURCE_RESPONSE" | jq -r '.id')
        
        if [[ -n "$GPU_RESOURCE_ID" && "$GPU_RESOURCE_ID" != "null" ]]; then
            echo_success "Created GPU resource: $GPU_RESOURCE_ID"
            echo "export GPU_RESOURCE_ID=$GPU_RESOURCE_ID" >> /tmp/test_vars.sh
        else
            echo_error "Failed to create GPU resource"
        fi
    fi
}

# Update tool to link resource requirements
link_tool_resources() {
    echo_step "Linking GPU tool to GPU resource..."
    
    source /tmp/test_vars.sh
    
    if [[ -z "$GPU_TOOL_ID" || -z "$GPU_RESOURCE_ID" ]]; then
        echo_error "Missing GPU_TOOL_ID or GPU_RESOURCE_ID"
        return 1
    fi
    
    # Update tool with resource requirement
    RESPONSE=$(curl -s -X PUT "$API_BASE/tools/$GPU_TOOL_ID" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "{
            \"resource_ids\": [\"$GPU_RESOURCE_ID\"],
            \"implementation_notes\": \"Configured with GPU resource requirement. Ready for interface setup.\"
        }")
    
    echo "$RESPONSE" | jq '{id, name, resource_ids, status}'
    echo_success "Linked GPU resource to tool"
}

# Show what's needed to move to implemented
show_implementation_checklist() {
    echo_step "Implementation Checklist"
    
    cat << 'EOF'

To move a tool from "implementing" to "implemented", we need:

For REST API Tools (like Mock GPU Image Generator):
□ base_url - URL to reach the API
□ endpoints - Map of operations to HTTP methods/paths
□ auth - Authentication method (none, api_key, bearer, etc.)
□ health_check - Endpoint to verify service is running
□ input_schema - JSON Schema for validating inputs
□ timeout - Max execution time

For CLI Tools (like Mock CLI Analyzer):
□ command - The command to execute
□ templates - Map of operations to argument templates
□ working_dir - Working directory for execution
□ timeout - Max execution time
□ input_schema - JSON Schema for validating inputs

Current Tool Executor supports hardcoded executors. We need to either:
1. Add executor methods for these mock tools, OR
2. Implement the database-driven configuration system

EOF
}

# Test tool execution (once implemented)
test_tool_execution() {
    echo_step "Testing tool execution..."
    
    source /tmp/test_vars.sh
    
    # Test GPU tool if implemented
    if [[ -n "$GPU_TOOL_ID" ]]; then
        echo "Testing GPU tool execution..."
        
        RESPONSE=$(curl -s -X POST "$API_BASE/tools/mock-gpu-imgen/execute" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -d '{
                "params": {
                    "prompt": "A beautiful sunset over mountains",
                    "model": "fast-diffusion"
                }
            }' 2>&1)
        
        if echo "$RESPONSE" | jq -e '.success' > /dev/null 2>&1; then
            echo_success "GPU tool execution succeeded:"
            echo "$RESPONSE" | jq '.'
        else
            echo_warn "GPU tool execution response:"
            echo "$RESPONSE" | jq '.' 2>/dev/null || echo "$RESPONSE"
        fi
    fi
    
    # Test CLI tool if implemented
    if [[ -n "$CLI_TOOL_ID" ]]; then
        echo "Testing CLI tool execution..."
        
        RESPONSE=$(curl -s -X POST "$API_BASE/tools/mock-cli-analyzer/execute" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -d '{
                "params": {
                    "operation": "analyze",
                    "input": "This is a test sentence for analysis"
                }
            }' 2>&1)
        
        if echo "$RESPONSE" | jq -e '.success' > /dev/null 2>&1; then
            echo_success "CLI tool execution succeeded:"
            echo "$RESPONSE" | jq '.'
        else
            echo_warn "CLI tool execution response:"
            echo "$RESPONSE" | jq '.' 2>/dev/null || echo "$RESPONSE"
        fi
    fi
}

# Show current tool status
show_tool_status() {
    echo_step "Current tool status..."
    
    source /tmp/test_vars.sh
    
    if [[ -n "$GPU_TOOL_ID" ]]; then
        echo "GPU Tool:"
        curl -s "$API_BASE/tools/$GPU_TOOL_ID" -H "Authorization: Bearer $TOKEN" | \
            jq '{name, slug, status, resource_ids, implementation_notes}'
    fi
    
    if [[ -n "$CLI_TOOL_ID" ]]; then
        echo "CLI Tool:"
        curl -s "$API_BASE/tools/$CLI_TOOL_ID" -H "Authorization: Bearer $TOKEN" | \
            jq '{name, slug, status, resource_ids, implementation_notes}'
    fi
}

# Cleanup test data
cleanup() {
    echo_step "Cleanup test data..."
    
    source /tmp/test_vars.sh
    
    read -p "Delete test tools and resources? (y/N) " confirm
    if [[ "$confirm" == "y" || "$confirm" == "Y" ]]; then
        if [[ -n "$GPU_TOOL_ID" ]]; then
            curl -s -X DELETE "$API_BASE/tools/$GPU_TOOL_ID" -H "Authorization: Bearer $TOKEN"
            echo_success "Deleted GPU tool"
        fi
        
        if [[ -n "$CLI_TOOL_ID" ]]; then
            curl -s -X DELETE "$API_BASE/tools/$CLI_TOOL_ID" -H "Authorization: Bearer $TOKEN"
            echo_success "Deleted CLI tool"
        fi
        
        # Don't delete resources - they might be used by other things
        
        # Clear vars
        > /tmp/test_vars.sh
        echo_success "Cleanup complete"
    else
        echo "Skipping cleanup"
    fi
}

# Main menu
main() {
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║         Tool Implementation Test Script                       ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    
    PS3="Select action: "
    options=(
        "Full setup (auth + mock API check + create tools)"
        "Authenticate only"
        "Test mock GPU API"
        "Create tool records"
        "Check resources"
        "Link GPU resource to tool"
        "Show implementation checklist"
        "Show tool status"
        "Test tool execution"
        "Cleanup"
        "Exit"
    )
    
    while true; do
        echo ""
        select opt in "${options[@]}"; do
            case $REPLY in
                1)
                    get_token
                    test_mock_gpu_api
                    create_tool_records
                    check_resources
                    link_tool_resources
                    show_implementation_checklist
                    break
                    ;;
                2) get_token; break ;;
                3) test_mock_gpu_api; break ;;
                4) create_tool_records; break ;;
                5) check_resources; break ;;
                6) link_tool_resources; break ;;
                7) show_implementation_checklist; break ;;
                8) show_tool_status; break ;;
                9) test_tool_execution; break ;;
                10) cleanup; break ;;
                11) echo "Goodbye!"; exit 0 ;;
                *) echo "Invalid option"; break ;;
            esac
        done
    done
}

# Run if not sourced
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
