"""Test HPE WSAPI authentication with KeePass credentials."""
import requests
import urllib3
urllib3.disable_warnings()

from app.services.keepass import get_credentials

# Test with HPE_sanadmin credentials on the first 3Par 20450
cred_keys = ["HPE_sanadmin", "HPE_3paradm"]
test_arrays = [
    ("tp24vf-cn.adc1.level3.com", "3par 20450", "ADC"),
    ("hpdnp003m.corp.intranet", "Primera A670", "DDC"),
    ("hpomp02bv.corp.intranet", "Primera A670", "ODC"),
    ("hpdnp05k6.corp.intranet", "Alletra 9080", "DDC"),
]

for fqdn, model, site in test_arrays:
    print(f"\n=== {fqdn} ({model} @ {site}) ===")
    for key in cred_keys:
        try:
            creds = get_credentials(key)
            user = creds.get("username", "")
            password = creds.get("password", "")

            # WSAPI auth: POST /api/v1/credentials
            resp = requests.post(
                f"https://{fqdn}:8080/api/v1/credentials",
                json={"user": user, "password": password},
                verify=False,
                timeout=10,
            )
            if resp.status_code == 201:
                data = resp.json()
                session_key = data.get("key", "")
                print(f"  {key:30s} AUTH OK! session_key={session_key[:20]}...")

                # Quick test: get system info
                headers = {"Authorization": f"Bearer {session_key}"}
                sys_resp = requests.get(
                    f"https://{fqdn}:8080/api/v1/system",
                    headers=headers, verify=False, timeout=10,
                )
                if sys_resp.status_code == 200:
                    sys_data = sys_resp.json()
                    print(f"    System: name={sys_data.get('name','?')} model={sys_data.get('model','?')} serialNumber={sys_data.get('serialNumber','?')}")
                    print(f"    Firmware: {sys_data.get('systemVersion','?')} totalCapacity={sys_data.get('totalCapacityMiB',0)} MiB")

                # Logout
                requests.delete(
                    f"https://{fqdn}:8080/api/v1/credentials/{session_key}",
                    headers=headers, verify=False, timeout=5,
                )
                break  # auth succeeded, no need to try other keys
            else:
                print(f"  {key:30s} HTTP {resp.status_code}: {resp.text[:80]}")
        except requests.ConnectionError:
            print(f"  {key:30s} Connection refused")
            break
        except requests.Timeout:
            print(f"  {key:30s} Timeout")
            break
        except Exception as e:
            print(f"  {key:30s} Error: {e}")
