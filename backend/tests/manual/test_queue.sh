#!/bin/bash
# Test queue timeout and wait_for_resource scenarios

# Get a token: curl -s -X POST http://localhost:8000/api/v1/auth/login -H 'Content-Type: application/json' -d '{"identifier":"admin@example.com","password":"YOUR_PASSWORD"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])'
TOKEN="${TOKEN:-YOUR_JWT_TOKEN_HERE}"
TOOL_ID="${TOOL_ID:-YOUR_TOOL_ID_HERE}"
BASE_URL="http://localhost:8000/api/v1/tools"

echo "=== Test 1: Queue Timeout (2s) ==="
echo "Starting blocking job..."
curl -s -X POST "$BASE_URL/$TOOL_ID/execute" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"params": {"prompt": "Blocking job"}}' > /tmp/blocking.json &

sleep 0.5

echo "Submitting job with 2s timeout..."
curl -s -X POST "$BASE_URL/$TOOL_ID/execute" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"params": {"prompt": "Timeout test"}, "queue_timeout": 2}' | tee /tmp/timeout.json | jq '{status, error, job_id, queue_position}'

wait
echo ""
echo "=== Test 2: wait_for_resource=false ==="
echo "Starting blocking job..."
curl -s -X POST "$BASE_URL/$TOOL_ID/execute" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"params": {"prompt": "Another blocking job"}}' > /tmp/blocking2.json &

sleep 0.5

echo "Submitting job with wait_for_resource=false..."
curl -s -X POST "$BASE_URL/$TOOL_ID/execute" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"params": {"prompt": "Async test"}, "wait_for_resource": false}' | tee /tmp/async.json | jq '{status, error, job_id, queue_position}'

wait
echo ""
echo "=== Done ==="
