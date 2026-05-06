"""Test HPE connectivity — try different ports and API paths for Primera/Alletra."""
import requests
import urllib3
urllib3.disable_warnings()

from app.services.keepass import get_credentials

# Test arrays that failed on port 8080
test_arrays = [
    ("hpdnp003m.corp.intranet", "Primera A670", "DDC"),
    ("hpomp02bv.corp.intranet", "Primera A670", "ODC"),
    ("hpdnp05k6.corp.intranet", "Alletra 9080", "DDC"),
]

# Also test using management IPs instead of FQDNs
test_ips = [
    ("151.119.93.196", "Primera A670 DDC (IP)"),
    ("151.117.35.42", "Primera A670 ODC (IP)"),
    ("151.116.116.215", "Alletra 9080 DDC (IP)"),
]

ports = [8080, 443, 8443]
paths = ["/api/v1/credentials", "/api/v1/system", "/"]

for host, label in [(a[0], f"{a[1]} {a[2]}") for a in test_arrays] + test_ips:
    print(f"\n=== {host} ({label}) ===")
    for port in ports:
        for path in paths[:1]:  # just test credentials path
            url = f"https://{host}:{port}{path}"
            try:
                resp = requests.get(url, verify=False, timeout=5)
                print(f"  {port}{path:30s} HTTP {resp.status_code}")
                break  # port works, no need to test more paths
            except requests.ConnectionError:
                pass
            except requests.Timeout:
                print(f"  {port}{path:30s} Timeout")
                break
            except Exception as e:
                print(f"  {port}{path:30s} {str(e)[:60]}")
                break
        else:
            continue
        break
    else:
        print(f"  All ports refused")

# Also try the 3Par that DID work — get system info
print(f"\n=== GET SYSTEM INFO FROM WORKING 3PAR ===")
creds = get_credentials("HPE_sanadmin")
resp = requests.post(
    "https://tp24vf-cn.adc1.level3.com:8080/api/v1/credentials",
    json={"user": creds["username"], "password": creds["password"]},
    verify=False, timeout=10,
)
if resp.status_code == 201:
    key = resp.json().get("key", "")
    print(f"  Session key: {key[:20]}...")

    # Get system info with session key in header
    sys_resp = requests.get(
        "https://tp24vf-cn.adc1.level3.com:8080/api/v1/system",
        headers={"X-HP3PAR-WSAPI-SessionKey": key},
        verify=False, timeout=10,
    )
    if sys_resp.status_code == 200:
        d = sys_resp.json()
        print(f"  Name: {d.get('name')}")
        print(f"  Model: {d.get('model')}")
        print(f"  Serial: {d.get('serialNumber')}")
        print(f"  System Version: {d.get('systemVersion')}")
        print(f"  Total Capacity MiB: {d.get('totalCapacityMiB')}")
        print(f"  Allocated Capacity MiB: {d.get('allocatedCapacityMiB')}")
        print(f"  Free Capacity MiB: {d.get('freeCapacityMiB')}")
        print(f"  Total Nodes: {d.get('totalNodes')}")
    else:
        print(f"  System info: HTTP {sys_resp.status_code} - {sys_resp.text[:100]}")

    requests.delete(
        f"https://tp24vf-cn.adc1.level3.com:8080/api/v1/credentials/{key}",
        headers={"X-HP3PAR-WSAPI-SessionKey": key},
        verify=False, timeout=5,
    )
