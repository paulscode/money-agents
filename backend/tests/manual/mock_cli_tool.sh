#!/bin/bash
#
# Mock CLI Tool - Simulates a command-line tool that processes data
#
# This simulates tools like ffmpeg, imagemagick, yt-dlp, etc.
# that take input, do some processing, and return results.
#
# Usage:
#   ./mock_cli_tool.sh --operation <op> [options]
#
# Operations:
#   info      - Return system info
#   process   - Process input data
#   convert   - Convert between formats
#   analyze   - Analyze input and return stats
#

set -e

VERSION="1.0.0"
TOOL_NAME="mock-cli-tool"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Helper functions
error() {
    echo -e "${RED}ERROR: $1${NC}" >&2
    exit 1
}

success() {
    echo -e "${GREEN}$1${NC}"
}

# Parse arguments
OPERATION=""
INPUT=""
OUTPUT=""
FORMAT=""
VERBOSE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --operation|-o)
            OPERATION="$2"
            shift 2
            ;;
        --input|-i)
            INPUT="$2"
            shift 2
            ;;
        --output|-O)
            OUTPUT="$2"
            shift 2
            ;;
        --format|-f)
            FORMAT="$2"
            shift 2
            ;;
        --verbose|-v)
            VERBOSE=true
            shift
            ;;
        --help|-h)
            cat << EOF
$TOOL_NAME v$VERSION - Mock CLI Tool for Testing

Usage: $0 --operation <op> [options]

Operations:
  info      System information (no input required)
  process   Process input text and return transformed
  convert   Simulate format conversion
  analyze   Analyze input and return statistics

Options:
  --input, -i     Input data or file path
  --output, -O    Output file path (optional)
  --format, -f    Output format (json, text, csv)
  --verbose, -v   Verbose output
  --help, -h      Show this help

Examples:
  $0 --operation info --format json
  $0 --operation process --input "Hello World" --format json
  $0 --operation analyze --input "Some text to analyze"
EOF
            exit 0
            ;;
        *)
            error "Unknown option: $1"
            ;;
    esac
done

# Validate operation
if [[ -z "$OPERATION" ]]; then
    error "Operation is required. Use --help for usage."
fi

# Set default format
FORMAT="${FORMAT:-json}"

# Operation handlers
do_info() {
    local hostname=$(hostname)
    local kernel=$(uname -r)
    local cpu_count=$(nproc 2>/dev/null || echo "unknown")
    local memory=$(free -h 2>/dev/null | awk '/^Mem:/ {print $2}' || echo "unknown")
    local disk=$(df -h / 2>/dev/null | awk 'NR==2 {print $4}' || echo "unknown")
    local uptime=$(uptime -p 2>/dev/null || echo "unknown")
    local timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    
    if [[ "$FORMAT" == "json" ]]; then
        cat << EOF
{
  "tool": "$TOOL_NAME",
  "version": "$VERSION",
  "timestamp": "$timestamp",
  "system": {
    "hostname": "$hostname",
    "kernel": "$kernel",
    "cpu_count": "$cpu_count",
    "memory_total": "$memory",
    "disk_available": "$disk",
    "uptime": "$uptime"
  },
  "capabilities": [
    "info",
    "process",
    "convert",
    "analyze"
  ]
}
EOF
    else
        echo "$TOOL_NAME v$VERSION"
        echo "Hostname: $hostname"
        echo "Kernel: $kernel"
        echo "CPUs: $cpu_count"
        echo "Memory: $memory"
        echo "Disk Available: $disk"
        echo "Uptime: $uptime"
    fi
}

do_process() {
    if [[ -z "$INPUT" ]]; then
        error "Input is required for process operation"
    fi
    
    # Simulate some processing (uppercase, word count, etc.)
    local processed=$(echo "$INPUT" | tr '[:lower:]' '[:upper:]')
    local word_count=$(echo "$INPUT" | wc -w)
    local char_count=$(echo -n "$INPUT" | wc -c)
    local timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    
    # Simulate processing time
    sleep 0.5
    
    if [[ "$FORMAT" == "json" ]]; then
        cat << EOF
{
  "success": true,
  "operation": "process",
  "timestamp": "$timestamp",
  "input": "$INPUT",
  "output": {
    "processed": "$processed",
    "transformations": ["uppercase"],
    "stats": {
      "word_count": $word_count,
      "char_count": $char_count
    }
  }
}
EOF
    else
        echo "Processed: $processed"
        echo "Words: $word_count"
        echo "Characters: $char_count"
    fi
    
    # Write to output file if specified
    if [[ -n "$OUTPUT" ]]; then
        echo "$processed" > "$OUTPUT"
        [[ "$VERBOSE" == true ]] && echo "Output written to: $OUTPUT" >&2
    fi
}

do_convert() {
    if [[ -z "$INPUT" ]]; then
        error "Input is required for convert operation"
    fi
    
    local input_format="text"
    local output_format="${FORMAT:-json}"
    local timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    
    # Simulate conversion time
    sleep 0.3
    
    if [[ "$output_format" == "json" ]]; then
        cat << EOF
{
  "success": true,
  "operation": "convert",
  "timestamp": "$timestamp",
  "conversion": {
    "from": "$input_format",
    "to": "$output_format",
    "input_size_bytes": ${#INPUT},
    "output_size_bytes": ${#INPUT}
  },
  "data": "$INPUT"
}
EOF
    elif [[ "$output_format" == "csv" ]]; then
        echo "data"
        echo "\"$INPUT\""
    else
        echo "$INPUT"
    fi
}

do_analyze() {
    if [[ -z "$INPUT" ]]; then
        error "Input is required for analyze operation"
    fi
    
    local word_count=$(echo "$INPUT" | wc -w)
    local char_count=$(echo -n "$INPUT" | wc -c)
    local line_count=$(echo "$INPUT" | wc -l)
    local unique_words=$(echo "$INPUT" | tr ' ' '\n' | sort -u | wc -l)
    local avg_word_len=$(echo "scale=2; $char_count / ($word_count + 1)" | bc 2>/dev/null || echo "0")
    local timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    
    # Detect content type
    local content_type="text"
    if echo "$INPUT" | grep -qE '^[0-9,.]+$'; then
        content_type="numeric"
    elif echo "$INPUT" | grep -qE '^https?://'; then
        content_type="url"
    fi
    
    # Simulate analysis time
    sleep 0.4
    
    if [[ "$FORMAT" == "json" ]]; then
        cat << EOF
{
  "success": true,
  "operation": "analyze",
  "timestamp": "$timestamp",
  "analysis": {
    "content_type": "$content_type",
    "statistics": {
      "characters": $char_count,
      "words": $word_count,
      "lines": $line_count,
      "unique_words": $unique_words,
      "avg_word_length": $avg_word_len
    },
    "sample": "${INPUT:0:50}..."
  }
}
EOF
    else
        echo "Content Type: $content_type"
        echo "Characters: $char_count"
        echo "Words: $word_count"
        echo "Lines: $line_count"
        echo "Unique Words: $unique_words"
        echo "Avg Word Length: $avg_word_len"
    fi
}

# Execute operation
case "$OPERATION" in
    info)
        do_info
        ;;
    process)
        do_process
        ;;
    convert)
        do_convert
        ;;
    analyze)
        do_analyze
        ;;
    *)
        error "Unknown operation: $OPERATION. Valid operations: info, process, convert, analyze"
        ;;
esac
