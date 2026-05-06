"""Debug Oracle ZFS API - get pool detail with usage."""
import requests, urllib3, json
urllib3.disable_warnings()
from app.services.keepass import get_credentials

creds = get_credentials("ZFS_root")
host = "oratp4161-c1.adc1.level3.com"
base = f"https://{host}:215/api"
auth = (creds["username"], creds["password"])

# Get pool detail
resp = requests.get(f"{base}/storage/v1/pools/Pool1", auth=auth, verify=False, timeout=10)
print(f"=== Pool1 detail (HTTP {resp.status_code}) ===")
if resp.status_code == 200:
    data = resp.json()
    print(json.dumps(data, indent=2, default=str)[:1000])

# Get pool usage
resp2 = requests.get(f"{base}/storage/v1/pools/Pool1/usage", auth=auth, verify=False, timeout=10)
print(f"\n=== Pool1/usage (HTTP {resp2.status_code}) ===")
if resp2.status_code == 200:
    print(json.dumps(resp2.json(), indent=2, default=str)[:500])

# Get projects
resp3 = requests.get(f"{base}/storage/v1/pools/Pool1/projects", auth=auth, verify=False, timeout=10)
print(f"\n=== Pool1/projects (HTTP {resp3.status_code}) ===")
if resp3.status_code == 200:
    data = resp3.json()
    print(json.dumps(data, indent=2, default=str)[:500])
