"""Explore Oracle ZFS arrays and test REST API connectivity."""
import requests
import urllib3
urllib3.disable_warnings()

from app.db.session import get_db_cursor, rows_to_dicts
from app.services.keepass import get_credentials

print("=== ORACLE ARRAYS IN MANAGED_ARRAYS ===")
with get_db_cursor() as cur:
    cur.execute(
        "SELECT array_name, array_fqdn, model, site, mgmt_ip "
        "FROM USM.managed_arrays "
        "WHERE vendor = 'oracle' AND disposition = 'Current' "
        "ORDER BY model, site"
    )
    rows = rows_to_dicts(cur, cur.fetchall())

for r in rows:
    print(f"  {r['array_name']:25s} FQDN={r.get('array_fqdn',''):45s} Model={r.get('model',''):12s} Site={r.get('site',''):6s} IP={r.get('mgmt_ip','')}")

print(f"\n=== ORACLE KEEPASS CREDENTIALS ===")
for key in ["ZFS_root", "ZFS_root_lCTL", "ZFS_oracle_agent"]:
    try:
        creds = get_credentials(key)
        user = creds.get("username", "")
        has_pw = "yes" if creds.get("password") else "no"
        print(f"  {key:25s} user={user:15s} has_password={has_pw}")
    except Exception as e:
        print(f"  {key:25s} ERROR: {e}")

print(f"\n=== ZFS REST API CONNECTIVITY TEST ===")
# Oracle ZFS REST API on port 215
test_arrays = rows[:4] if rows else []
for r in test_arrays:
    fqdn = r.get("array_fqdn") or r.get("mgmt_ip") or r["array_name"]
    print(f"\n  Testing: {fqdn} ({r.get('model','')})...")
    
    for port in [215, 443]:
        for path in ["/api/storage/v1", "/api/system/v1/version"]:
            url = f"https://{fqdn}:{port}{path}"
            try:
                resp = requests.get(url, verify=False, timeout=5)
                print(f"    {port}{path}: HTTP {resp.status_code}")
                if resp.status_code == 401:
                    # Try with auth
                    creds = get_credentials("ZFS_root")
                    resp2 = requests.get(
                        url,
                        auth=(creds["username"], creds["password"]),
                        verify=False, timeout=10,
                    )
                    print(f"    {port}{path} (auth): HTTP {resp2.status_code} — {resp2.text[:120]}")
                    if resp2.status_code == 200:
                        break
                elif resp.status_code == 200:
                    print(f"    {port}{path}: {resp.text[:120]}")
                    break
            except requests.ConnectionError:
                pass
            except requests.Timeout:
                print(f"    {port}{path}: Timeout")
            except Exception as e:
                print(f"    {port}{path}: {str(e)[:60]}")
