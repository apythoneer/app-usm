"""Debug Oracle ZFS API structure for pools/projects/luns/filesystems."""
import requests, urllib3, json, sys
urllib3.disable_warnings()

from app.services.keepass import get_credentials
from app.db.session import get_db_cursor, rows_to_dicts

# Get first enabled oracle array
with get_db_cursor() as cur:
    cur.execute(
        "SELECT array_name, array_fqdn, mgmt_ip, cred_key "
        "FROM USM.managed_arrays "
        "WHERE vendor='oracle' AND enabled=1 ORDER BY array_name"
    )
    rows = rows_to_dicts(cur, cur.fetchall())

if not rows:
    print("No enabled oracle arrays found")
    sys.exit(1)

arr = rows[0]
print(f"Testing: {arr['array_name']} (fqdn={arr.get('array_fqdn','')})")
fqdn = arr.get("array_fqdn") or arr.get("mgmt_ip") or arr["array_name"]
cred_key = arr.get("cred_key", "ZFS_root")
creds = get_credentials(cred_key)
base = f"https://{fqdn}:215/api"
auth = (creds["username"], creds["password"])

# 1) Pools list
print("\n=== GET /storage/v1/pools ===")
r = requests.get(f"{base}/storage/v1/pools", auth=auth, verify=False, timeout=15)
pools_data = r.json()
pools = pools_data.get("pools", [])
print(f"  Count: {len(pools)}")
if pools:
    p0 = pools[0]
    print(f"  First item keys: {sorted(p0.keys())}")
    print(f"  First item (truncated): {json.dumps(p0, default=str)[:300]}")
    # Check if nested or flat
    if "name" in p0:
        print(f"  -> FLAT format: name='{p0['name']}', status='{p0.get('status','')}'")
    elif "pool" in p0:
        print(f"  -> NESTED format: pool keys={sorted(p0['pool'].keys())}")

# Find first online pool
pool_name = None
for p in pools:
    name = p.get("name") or (p.get("pool", {}) or {}).get("name", "")
    status = p.get("status") or (p.get("pool", {}) or {}).get("status", "")
    print(f"  Pool: {name}, status: {status}")
    if status == "online" and not pool_name:
        pool_name = name

if not pool_name:
    print("No online pool found!")
    sys.exit(1)

# 2) Projects list
print(f"\n=== GET /storage/v1/pools/{pool_name}/projects ===")
r = requests.get(f"{base}/storage/v1/pools/{pool_name}/projects", auth=auth, verify=False, timeout=15)
projs_data = r.json()
projs = projs_data.get("projects", [])
print(f"  Count: {len(projs)}")
if projs:
    pr0 = projs[0]
    print(f"  First item keys: {sorted(pr0.keys())}")
    print(f"  First item (truncated): {json.dumps(pr0, default=str)[:300]}")
    if "name" in pr0:
        print(f"  -> FLAT format: name='{pr0['name']}'")
    elif "project" in pr0:
        print(f"  -> NESTED format: project keys={sorted(pr0['project'].keys())}")

# Find first project name
proj_name = None
for pr in projs:
    pn = pr.get("name") or (pr.get("project", {}) or {}).get("name", "")
    if pn:
        proj_name = pn
        break

if not proj_name:
    print("No projects found!")
    sys.exit(1)

# 3) LUNs
print(f"\n=== GET /storage/v1/pools/{pool_name}/projects/{proj_name}/luns ===")
r = requests.get(f"{base}/storage/v1/pools/{pool_name}/projects/{proj_name}/luns", auth=auth, verify=False, timeout=15)
luns_data = r.json()
luns = luns_data.get("luns", [])
print(f"  Count: {len(luns)}")
if luns:
    l0 = luns[0]
    print(f"  First item keys: {sorted(l0.keys())}")
    print(f"  First item (truncated): {json.dumps(l0, default=str)[:400]}")
    if "name" in l0:
        print(f"  -> FLAT format: name='{l0['name']}'")
    elif "lun" in l0:
        print(f"  -> NESTED format: lun keys={sorted(l0['lun'].keys())}")

# 4) Filesystems
print(f"\n=== GET /storage/v1/pools/{pool_name}/projects/{proj_name}/filesystems ===")
r = requests.get(f"{base}/storage/v1/pools/{pool_name}/projects/{proj_name}/filesystems", auth=auth, verify=False, timeout=15)
fs_data = r.json()
fss = fs_data.get("filesystems", [])
print(f"  Count: {len(fss)}")
if fss:
    f0 = fss[0]
    print(f"  First item keys: {sorted(f0.keys())}")
    print(f"  First item (truncated): {json.dumps(f0, default=str)[:400]}")
    if "name" in f0:
        print(f"  -> FLAT format: name='{f0['name']}'")
    elif "filesystem" in f0:
        print(f"  -> NESTED format: filesystem keys={sorted(f0['filesystem'].keys())}")

print("\nDone.")
