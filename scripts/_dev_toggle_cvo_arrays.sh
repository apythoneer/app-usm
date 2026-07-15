#!/usr/bin/env bash
# Enable/disable the 11 cloud CVO NetApp arrays that are unreachable from the
# monitoring host (usodclpsandadm1).
#
# Disabled 2026-07-15 pending network-team verification of routing/DNS.
#
# WHY these are disabled (verified, not assumed):
#   - The KeePass entries are FINE. CVO_GCP / CVO_Azure / CVO_AWS all resolve
#     from the credential API. This is NOT a credentials problem, despite the
#     logs having said "Authentication failed" for a month — authenticate()
#     caught every exception and mislabelled DNS errors as auth failures
#     (fixed separately in netapp/client.py).
#   - 7 GCP CVOs: array_fqdn does not resolve. Four point at a DIFFERENT host
#     than their own array_name, and gcpbluse4adbldv01.corp.intranet is assigned
#     to THREE separate arrays.
#   - 4 Azure CVOs: array_fqdn resolves but the host is not routable from the
#     monitoring box.
#
# IMPORTANT — do not "fix" this by editing array_fqdn by hand:
#   inventory_sync (cron 03:00) overwrites array_fqdn from
#   StorMart.dbo.DimStorageFinance on every run, so a manual edit is reverted
#   overnight. The bad FQDNs must be corrected in DimStorageFinance itself.
#   `enabled` is NOT written by inventory_sync's UPDATE, which is why toggling
#   it here is durable.
#
# Usage:
#   bash _dev_toggle_cvo_arrays.sh disable
#   bash _dev_toggle_cvo_arrays.sh enable    # after network team confirms routing
#   bash _dev_toggle_cvo_arrays.sh status
#
# After `enable`, watch the next cycle (metrics runs every 300s):
#   docker logs --since 6m usm-backend 2>&1 | grep -E "gcpus|NetAppCVO"
# A working array logs "Collection complete"; a broken one now logs
# "Cannot reach '<host>' (name does not resolve)" instead of a bogus auth error.
set -euo pipefail

API=http://localhost:8000/api/v1

ARRAYS=(
  # Azure CVO — FQDN resolves, host not routable from the monitoring box
  NetAppCVOARCProdEUS203
  NetAppCVOETLNPEUS203
  NetAppCVOETLNPEUS204
  NetAppCVOGPProdEUS205
  # GCP CVO — array_fqdn does not resolve (several point at the wrong host)
  gcpusc1acvobldr01
  gcpusc1acvofidr01
  gcpuse4acvodbldv01
  gcpuse4acvofidv01
  gcpuse4acvofitp01
  gcpuse4bcvoblpd01
  gcpuse4bcvofipd01
)

# NOTE: deliberately NOT included — these CVOs work and must stay enabled:
#   NetAppCVOETLPRODEUS202, NetappCVOGPProdEUS201,
#   NetappCVOInformaticaNonProdCUS01, NetappDRAWSCVOUSE1A,
#   NetappGPCVOPdAWSUSE2A, NetappNPAWSCVOUSE2A

action="${1:-status}"

case "$action" in
  enable|disable)
    val=$([ "$action" = "enable" ] && echo true || echo false)
    echo "==> setting enabled=$val on ${#ARRAYS[@]} CVO arrays"
    for a in "${ARRAYS[@]}"; do
      code=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "$API/arrays/managed/$a" \
        -H 'Content-Type: application/json' -d "{\"enabled\": $val}")
      printf '   %-26s -> HTTP %s\n' "$a" "$code"
    done
    ;;
  status) ;;
  *) echo "usage: $0 [enable|disable|status]"; exit 1 ;;
esac

echo
echo "==> current state"
curl -sf "$API/arrays/managed" | python3 -c '
import sys, json
want = set("""'"${ARRAYS[*]}"'""".split())
d = json.load(sys.stdin)
rows = d if isinstance(d, list) else d.get("data", [])
for x in sorted(rows, key=lambda r: r.get("array_name", "")):
    if x.get("array_name") in want:
        print("   %-26s enabled=%s" % (x["array_name"], x.get("enabled")))
'
