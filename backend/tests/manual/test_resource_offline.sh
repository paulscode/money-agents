#!/bin/bash
# Test resource offline with jobs in queue

# Get a token: curl -s -X POST http://localhost:8000/api/v1/auth/login -H 'Content-Type: application/json' -d '{"identifier":"admin@example.com","password":"YOUR_PASSWORD"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])'
TOKEN="${TOKEN:-YOUR_JWT_TOKEN_HERE}"
TOOL_ID="${TOOL_ID:-YOUR_TOOL_ID_HERE}"
RESOURCE_ID="${RESOURCE_ID:-YOUR_RESOURCE_ID_HERE}"
BASE_URL="http://localhost:8000/api/v1"

echo "=== Test: Resource offline with jobs in queue ==="
echo ""

# Submit 3 jobs (first will start running, others queue)
echo "1. Submitting 3 jobs..."
for i in 1 2 3; do
  curl -s -X POST "$BASE_URL/tools/$TOOL_ID/execute" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $TOKEN" \
    -d "{\"params\": {\"prompt\": \"Job $i\"}}" > /tmp/job_$i.json &
done

sleep 1

# Check queue state
echo ""
echo "2. Queue state after submitting jobs:"
curl -s "$BASE_URL/resources/$RESOURCE_ID/queue" \
  -H "Authorization: Bearer $TOKEN" | jq '[.[] | {id: .id[:8], status, queued_at: .queued_at[11:19]}]'

# Put resource into maintenance
echo ""
echo "3. Setting resource to maintenance..."
curl -s -X PATCH "$BASE_URL/resources/$RESOURCE_ID/status" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"status": "maintenance"}' | jq '{name, status}'

# Check queue state after
echo ""
echo "4. Queue state after maintenance (should show cancelled):"
docker exec money-agents-postgres psql -U money_agents -d money_agents -c \
  "SELECT id::text, status, error FROM job_queue WHERE resource_id = '$RESOURCE_ID' ORDER BY queued_at DESC LIMIT 5;" 2>/dev/null

# Wait for background jobs
wait

# Check job results
echo ""
echo "5. Job results:"
for i in 1 2 3; do
  echo "Job $i: $(cat /tmp/job_$i.json | jq -r '{status, error: (.error // "none")[:50]}')"
done

# Restore resource
echo ""
echo "6. Restoring resource to available..."
curl -s -X PATCH "$BASE_URL/resources/$RESOURCE_ID/status" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"status": "available"}' | jq '{name, status}'

# Verify it works again
echo ""
echo "7. Verify tool works after restore..."
curl -s -X POST "$BASE_URL/tools/$TOOL_ID/execute" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"params": {"prompt": "After restore"}}' | jq '{status, duration_ms}'

echo ""
echo "=== Done ==="
