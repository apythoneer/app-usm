#!/usr/bin/env bash
# Deploy the 2026-07-15 hardening branch to the dev host.
#
# This is NOT the usual cherry-pick flow. The host had diverged in BOTH
# directions, so the repo was first made a true superset (84f3e54 rescued the
# host-only nginx.conf / Makefile / .gitignore work). This script therefore
# switches the host onto the repo branch wholesale, which:
#   - ships the hardening fixes (10 commits)
#   - FIXES backend/app/collectors/base.py, where the 06-19 merge (4afa298)
#     nested the batched executemany inside the `except` handler, so the SQLite
#     half of the P1 perf fix has never actually run in production
#   - drops the nested duplicate trees (frontend/frontend, backend/backend,
#     docker/backend, docker/docker, frontend/config) committed by bad scp's
#
# backend/app/collectors/hitachi_old/ is deliberately RESTORED afterwards — it
# is dead code (its __init__ imports the real app.collectors.hitachi package, so
# its own VENDOR="hitachi" classes never register) but removal was explicitly
# out of scope for this change.
#
# Usage (from your workstation):
#   git bundle create /tmp/usm-hardening.bundle feature/capacity-overhaul
#   scp /tmp/usm-hardening.bundle scripts/_dev_deploy_hardening.sh <host>:/tmp/
#   ssh <host> 'bash /tmp/_dev_deploy_hardening.sh'
set -euo pipefail

REPO=/home/ad64490/unified_storage_monitoring
COMPOSE="$REPO/docker/docker-compose.yml"
BUNDLE=/tmp/usm-hardening.bundle
BRANCH=server-hardening-2026-07-15
cd "$REPO"

echo "=== PREFLIGHT: KEEPASS_PASSWORD must be set ==="
# The hardcoded vault password fallback was removed from docker-compose.yml.
# Without this in .env, `docker compose up` aborts and takes the whole stack
# down — including the credential API every collector depends on.
if ! grep -q '^KEEPASS_PASSWORD=' .env 2>/dev/null; then
  echo "!! ABORT: .env has no KEEPASS_PASSWORD."
  echo "!! docker compose would refuse to start and take the stack down."
  echo "!! Add it to $REPO/.env first, then re-run."
  exit 1
fi
echo "  KEEPASS_PASSWORD: set"

echo "=== current state ==="
git rev-parse --abbrev-ref HEAD
git log --oneline -1
OLD_REF=$(git rev-parse HEAD)

TAG="pre-hardening-$(date +%Y%m%d-%H%M%S)"
git tag -f "$TAG"
echo "rollback tag: $TAG  ($OLD_REF)"

echo "=== fetch bundle ==="
git fetch "$BUNDLE" 'refs/heads/feature/capacity-overhaul:refs/remotes/bundle/hardening'
NEW=$(git rev-parse refs/remotes/bundle/hardening)
echo "target commit: $NEW"

echo "=== switch onto the hardening branch ==="
git checkout -B "$BRANCH" refs/remotes/bundle/hardening

echo "=== restore hitachi_old (dead code, but out of scope for this change) ==="
if git show "$OLD_REF:backend/app/collectors/hitachi_old/__init__.py" >/dev/null 2>&1; then
  git checkout "$OLD_REF" -- backend/app/collectors/hitachi_old
  git commit -q -m "chore: restore hitachi_old (dead code, removal out of scope)" \
    -m "Carried over from $OLD_REF. Its __init__ imports app.collectors.hitachi, so its own VENDOR='hitachi' classes never register — inert, but not this change's call to delete." \
    || echo "  (nothing to commit)"
  echo "  restored"
else
  echo "  not present in $OLD_REF — skipping"
fi

echo "=== SANITY: base.py merge damage is fixed ==="
if awk '/except Exception as e:/{f=NR} /# Batched executemany/{if(f && NR-f<6){print "BAD: batched block still inside except (line " NR ")"; exit 1}}' backend/app/collectors/base.py; then
  echo "  base.py: batched block is NOT inside the except handler — OK"
fi
python3 -m py_compile backend/app/collectors/base.py && echo "  base.py compiles"

echo "=== SANITY: empty-collect guards present (data-loss fix) ==="
grep -q "keeping existing data" backend/app/collectors/pure/volumes.py   && echo "  pure/volumes.py: guard present"
grep -q "keeping existing data" backend/app/collectors/netapp/volumes.py && echo "  netapp/volumes.py: guard present"

echo "=== SANITY: fleet-stats no longer reads SQLite ==="
grep -q "fetch_fleet_stats" backend/app/db/cache.py && echo "  !! cache.fetch_fleet_stats still present" || echo "  cache.fetch_fleet_stats removed — OK"

echo "=== SANITY: cruft trees gone ==="
for d in frontend/frontend backend/backend docker/backend docker/docker frontend/config; do
  [ -e "$d" ] && echo "  !! $d STILL PRESENT" || echo "  $d removed"
done

echo "=== SANITY: openpyxl declared (capacity-export.xlsx) ==="
grep -q "^openpyxl" backend/requirements.txt && echo "  openpyxl in requirements"

echo "=== rebuild: backend + frontend + keepass ==="
# keepass rebuilds because the hardcoded password default was removed from
# keepass_app.py; frontend because of the UI fixes; backend for the rest.
docker compose -f "$COMPOSE" up -d --build usm-backend usm-frontend usm-keepass

echo "=== wait for health ==="
for svc in usm-keepass usm-backend usm-frontend; do
  for i in $(seq 1 40); do
    h=$(docker inspect --format '{{.State.Health.Status}}' "$svc" 2>/dev/null || echo none)
    [ "$h" = "healthy" ] && { echo "  $svc: healthy"; break; }
    [ "$i" = "40" ] && echo "  !! $svc: NOT healthy (last=$h)"
    sleep 3
  done
done

echo "=== RE-VALIDATE against the running stack ==="
echo "--- containers ---"
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep usm || true

echo "--- keepass still serving credentials? (the risky bit) ---"
curl -sf http://localhost:2000/health >/dev/null && echo "  keepass /health OK" || echo "  !! keepass /health FAILED"

echo "--- backend health ---"
curl -sf http://localhost:8000/health | head -c 300; echo

echo "--- THE HEADLINE FIX: fleet-stats active_alerts should now be REAL, not 0 ---"
curl -sf http://localhost:8000/api/v1/arrays/fleet-stats \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print("   active_alerts:", d.get("active_alerts"), "| arrays:", d.get("total_arrays"), "| volumes:", d.get("total_volumes")); print("   ^^ if active_alerts is 0 AND you have open alerts, the fix did not take")' \
  || echo "  !! fleet-stats failed"

echo "--- capacity page + endpoint ---"
curl -s -o /dev/null -w "   GET /capacity -> HTTP %{http_code}\n" http://localhost:8080/capacity
curl -s -o /dev/null -w "   GET /analytics/capacity-breakdown -> HTTP %{http_code}\n" http://localhost:8000/api/v1/analytics/capacity-breakdown

echo "--- capacity-export.xlsx (was 500 — openpyxl was undeclared) ---"
curl -s -o /dev/null -w "   GET /analytics/capacity-export.xlsx -> HTTP %{http_code}\n" http://localhost:8000/api/v1/analytics/capacity-export.xlsx

echo "--- scheduler jobs ---"
curl -sf http://localhost:8000/api/v1/scheduler/status \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); j=sorted(x["id"] for x in d.get("jobs",[])); print("   ", len(j), "jobs:", j)' \
  || echo "  !! scheduler status failed"

echo
echo "DONE."
echo "Rollback:"
echo "  cd $REPO && git checkout $TAG && docker compose -f $COMPOSE up -d --build usm-backend usm-frontend usm-keepass"
