"""Explore HPE arrays in managed_arrays and test WSAPI connectivity."""
import requests
import urllib3
urllib3.disable_warnings()

from app.db.session import get_db_cursor, rows_to_dicts
from app.services.keepass import get_credentials
from app.core.config import get_settings

settings = get_settings()
SCHEMA = settings.db_schema

print("=== HPE ARRAYS IN MANAGED_ARRAYS ===")
with get_db_cursor() as cur:
    cur.execute(
        f"SELECT array_name, array_fqdn, model, site, cred_key, mgmt_ip, enabled "
        f"FROM {SCHEMA}.managed_arrays "
        f"WHERE vendor = 'hpe' AND disposition = 'Current' "
        f"ORDER BY model, site"
    )
    rows = rows_to_dicts(cur, cur.fetchall())

for r in rows:
    print(f"  {r['array_name']:25s} FQDN={r.get('array_fqdn',''):40s} Model={r.get('model',''):20s} Site={r.get('site',''):6s} IP={r.get('mgmt_ip',''):16s} Key={r.get('cred_key','')}")

print(f"\n=== HPE KEEPASS CREDENTIALS ===")
hpe_keys = ["SSMC", "HPE_sanadmin", "HPE_3paradm", "HPE_Primera_3paradm"]
for key in hpe_keys:
    try:
        creds = get_credentials(key)
        user = creds.get("username", "")
        has_pw = "yes" if creds.get("password") else "no"
        print(f"  {key:30s} user={user:20s} has_password={has_pw}")
    except Exception as e:
        print(f"  {key:30s} ERROR: {e}")

print(f"\n=== WSAPI CONNECTIVITY TEST ===")
# HPE 3Par/Primera WSAPI runs on port 8080 with Basic Auth
# Try the first HPE array with Primera credentials
if rows:
    test_arr = rows[0]
    fqdn = test_arr.get("array_fqdn") or test_arr.get("mgmt_ip") or test_arr["array_name"]
    print(f"  Testing: {fqdn}")

    # Try SSMC port 8443 first (management console)
    for port in [8080, 443, 8443]:
        url = f"https://{fqdn}:{port}/api/v1/credentials"
        try:
            resp = requests.get(url, verify=False, timeout=5)
            print(f"  Port {port}: HTTP {resp.status_code} — {resp.text[:100]}")
        except requests.ConnectionError:
            print(f"  Port {port}: Connection refused")
        except requests.Timeout:
            print(f"  Port {port}: Timeout")
        except Exception as e:
            print(f"  Port {port}: {e}")
