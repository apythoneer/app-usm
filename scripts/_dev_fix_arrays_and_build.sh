#!/usr/bin/env bash
# Fix the corrupted arrays.ts import (committed in bad merge 4afa298 on 06-19,
# which left a split/duplicate 'import type ... from ./types' block), then
# rebuild usm-frontend.
set -euo pipefail

REPO=/home/ad64490/unified_storage_monitoring
COMPOSE="$REPO/docker/docker-compose.yml"
cd "$REPO"

echo "=== before (broken) head of arrays.ts ==="
sed -n '1,10p' frontend/src/api/arrays.ts

echo "=== install corrected arrays.ts ==="
cp /tmp/arrays.ts.fixed frontend/src/api/arrays.ts

echo "=== after (fixed) head of arrays.ts ==="
sed -n '1,10p' frontend/src/api/arrays.ts

echo "=== git diff summary ==="
git --no-pager diff --stat -- frontend/src/api/arrays.ts

echo "=== commit the fix ==="
git -c user.name='Aman Patel' -c user.email='aman.patel@lumen.com' \
  commit -m "fix(frontend): repair corrupted arrays.ts import block from 06-19 merge

The 2026-06-19 'merge volume-growth-history into server snapshot' (4afa298)
left a split/duplicate 'import type { ... } from ./types' that broke
'npm run build' (TS1109/TS1434 at arrays.ts:8). Restore the single,
correct import block so the frontend compiles." \
  -- frontend/src/api/arrays.ts
git --no-pager log --oneline -3

echo "=== rebuild + recreate usm-frontend ==="
docker compose -f "$COMPOSE" up -d --build usm-frontend

echo "=== wait for health ==="
for i in $(seq 1 40); do
  h=$(docker inspect --format '{{.State.Health.Status}}' usm-frontend 2>/dev/null || echo "none")
  echo "  attempt $i: health=$h"
  [ "$h" = "healthy" ] && break
  sleep 3
done

echo "=== RE-VALIDATE ==="
echo "container status: $(docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' usm-frontend)"
echo -n "frontend HTTP: "
curl -sf -o /dev/null -w '%{http_code}\n' http://localhost:8080 || echo "NOT reachable"
echo "DONE."
