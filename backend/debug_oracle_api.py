"""Debug Oracle ZFS API - explore actual response structure."""
import requests, urllib3, json
urllib3.disable_warnings()
from app.services.keepass import get_credentials

creds = get_credentials("ZFS_root")
host = "oratp4161-c1.adc1.level3.com"
base = f"https://{host}:215/api"
auth = (creds["username"], creds["password"])

endpoints = [
    "storage/v1",
    "storage/v1/pools",
    "system/v1/version",
    "problem/v1/problems",
    "system/v1/alerts",
]

for ep in endpoints:
    try:
        resp = requests.get(f"{base}/{ep}", auth=auth, verify=False, timeout=10)
        print(f"\n=== {ep} (HTTP {resp.status_code}) ===")
        if resp.status_code == 200:
            data = resp.json()
            print(json.dumps(data, indent=2, default=str)[:500])
        else:
            print(resp.text[:200])
    except Exception as e:
        print(f"  ERROR: {e}")
