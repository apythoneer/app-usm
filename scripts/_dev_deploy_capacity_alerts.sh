#!/usr/bin/env bash
# Deploy capacity alerting (per-array thresholds + fleet projected-full via
# Teams) to dev. Backend-only change: app/services/capacity_alerts.py +
# capacity_projection.py, scheduler.py job registration, settings.py surface
# + manual trigger endpoint, config.py CAPACITY_ALERT_* settings.
#
# Mirrors the proven _dev_deploy_capacity_overhaul.sh flow: dev history has
# diverged, so we cherry-pick the single commit from a bundle rather than
# reset/merge.
#
# Usage (from your workstation):
#   git bundle create usm-capacity-alerts.bundle feature/capacity-overhaul
#   scp scripts/_dev_deploy_capacity_alerts.sh usm-capacity-alerts.bundle \
#       ad64490@<dev-host>:/tmp/
#   ssh ad64490@<dev-host> 'bash /tmp/_dev_deploy_capacity_alerts.sh'
set -euo pipefail

REPO=/home/ad64490/unified_storage_monitoring
COMPOSE="$REPO/docker/docker-compose.yml"
BUNDLE=/tmp/usm-capacity-alerts.bundle
cd "$REPO"

echo "=== current state ==="
git rev-parse --abbrev-ref HEAD
git log --oneline -1

TAG="pre-capalerts-$(date +%Y%m%d-%H%M%S)"
git tag -f "$TAG"
echo "rollback tag: $TAG"

echo "=== fetch bundle ==="
git fetch "$BUNDLE" \
  'refs/heads/feature/capacity-overhaul:refs/remotes/bundle/capalerts'
FIX=$(git rev-parse refs/remotes/bundle/capalerts)
echo "fix commit: $FIX"

echo "=== cherry-pick the capacity-alerts commit ==="
if git cherry-pick "$FIX"; then
  echo "cherry-pick OK"
else
  echo "!! cherry-pick conflict — aborting and rolling back"
  git cherry-pick --abort || true
  exit 1
fi
git log --oneline -2

echo "=== sanity: alert service + scheduler job + settings present in source ==="
grep -n "def run_capacity_alert_checks" backend/app/services/capacity_alerts.py
grep -n "capacity_alerts" backend/app/collectors/scheduler.py | head
grep -n "capacity_alerts_enabled\|capacity-alerts/run" backend/app/api/v1/settings.py | head

echo "=== ensure .env has CAPACITY_ALERT_* (append defaults if missing) ==="
ENV_FILE="$REPO/.env"
if [ -f "$ENV_FILE" ] && ! grep -q '^CAPACITY_ALERTS_ENABLED=' "$ENV_FILE"; then
  cat >> "$ENV_FILE" <<'EOF'

# ---- Capacity alerting (added by _dev_deploy_capacity_alerts.sh) ----
CAPACITY_ALERTS_ENABLED=true
CAPACITY_ALERT_THRESHOLDS=80,90,95
CAPACITY_ALERT_RESEND_DAYS=7
CAPACITY_PROJECTED_FULL_DAYS=30
CAPACITY_ALERT_TREND_DAYS=90
CAPACITY_ALERT_CHECK_INTERVAL_HOURS=12
EOF
  echo "appended CAPACITY_ALERT_* defaults to .env"
else
  echo ".env already has CAPACITY_ALERTS_ENABLED (or .env missing) — leaving as-is"
fi

echo "=== rebuild + recreate usm-backend only (backend-only change) ==="
docker compose -f "$COMPOSE" up -d --build usm-backend

echo "=== wait for backend health ==="
for i in $(seq 1 40); do
  h=$(docker inspect --format '{{.State.Health.Status}}' usm-backend 2>/dev/null || echo "none")
  echo "  backend attempt $i: health=$h"
  [ "$h" = "healthy" ] && break
  sleep 3
done

echo "=== RE-VALIDATE running container ==="
echo "backend: $(docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' usm-backend)"

echo "--- scheduler: capacity_alerts job registered? ---"
curl -sf http://localhost:8000/api/v1/scheduler/status \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); jobs=[j["id"] for j in d.get("jobs",[])]; print("capacity_alerts job present:", "capacity_alerts" in jobs); print(jobs)' \
  || echo "!! scheduler status check failed"

echo "--- settings: capacity alert config surfaced? ---"
curl -sf http://localhost:8000/api/v1/settings \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print({k:v for k,v in d.items() if k.startswith("capacity_alert") or k=="capacity_alerts_enabled"})' \
  || echo "!! settings check failed"

echo "--- manual trigger: POST /settings/capacity-alerts/run ---"
curl -sf -X POST http://localhost:8000/api/v1/settings/capacity-alerts/run \
  | python3 -m json.tool \
  || echo "!! manual trigger failed"

echo "DONE."
echo "Rollback: cd $REPO && git reset --hard $TAG && docker compose -f $COMPOSE up -d --build usm-backend"
