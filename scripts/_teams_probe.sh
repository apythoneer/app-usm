#!/usr/bin/env bash
# Diagnostic: send the FLAT payload shape that matches the Power Automate flow
# (keys: array_name, severity, event, message_id, component, opened).
set -u
REPO=/home/ad64490/unified_storage_monitoring
URL=$(grep -E '^TEAMS_WEBHOOK_URL=' "$REPO/.env" | head -1 | cut -d= -f2-)
if [ -z "$URL" ]; then echo "no TEAMS_WEBHOOK_URL"; exit 1; fi

echo "===== FLAT (matches flow schema) ====="
code=$(curl -sS -o /tmp/_r.txt -w '%{http_code}' -H 'Content-Type: application/json' -X POST "$URL" --data '{
  "array_name":"USM-PROBE-FLAT",
  "vendor":"pure",
  "severity":"CRITICAL",
  "event":"PROBE D — flat keys matching flow",
  "component":"controller-0",
  "message_id":"12345",
  "opened":"2026-06-23 14:50 UTC"
}')
echo "HTTP $code"
echo "resp: $(head -c 300 /tmp/_r.txt)"
rm -f /tmp/_r.txt
echo
echo "Check Teams: did a card titled with array USM-PROBE-FLAT show all fields populated?"
