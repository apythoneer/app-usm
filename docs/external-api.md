# SIP External API (read-only)

A narrow, token-authenticated surface for partner applications to fetch **hosts**
and **volumes** from the Storage Intelligence Platform. Read-only; served under
`/api/ext/v1`, separate from the internal dashboard API.

- **Base URL (corp network):** `http://usodclpsandadm1.corp.intranet:8080/api/ext/v1`
  (use the HTTPS front door if/when TLS is terminated upstream — send keys over TLS only).
- **Auth:** every request needs an API key, sent as either header:
  - `Authorization: Bearer <key>`  — or —
  - `X-API-Key: <key>`
- Keys are **scoped** (`hosts:read`, `volumes:read`) and **rate-limited**
  (default 120 requests/min/key).

## Endpoints

### `GET /hosts`  — scope `hosts:read`
Query params (all optional): `array_name`, `vendor`, `search`, `limit` (1–1000, default 100), `offset`.

```json
{
  "total": 1423,
  "limit": 100,
  "offset": 0,
  "data": [
    {
      "array_name": "purecbs-aws-prod-gp-eus2a-04",
      "vendor": "pure",
      "host_name": "esx-prod-17",
      "iqn": "iqn.1998-01.com.vmware:esx-prod-17",
      "wwn": null,
      "nqn": null,
      "host_group": "vmware-prod",
      "volumes": ["vol-db-01", "vol-db-02"],
      "last_updated": "2026-08-18 14:05:11"
    }
  ]
}
```
Each host lists its attached `volumes` — use it to correlate hosts → volumes.

### `GET /volumes`  — scope `volumes:read`
Same query params as `/hosts`.

```json
{
  "total": 20488,
  "limit": 100,
  "offset": 0,
  "data": [
    {
      "array_name": "purecbs-aws-prod-gp-eus2a-04",
      "vendor": "pure",
      "volume_name": "vol-db-01",
      "size_bytes": 10995116277760,
      "used_bytes": 7834127138816,
      "data_reduction": 3.2,
      "snapshots": 4,
      "serial": "…",
      "hosts": ["esx-prod-17"],
      "host_groups": ["vmware-prod"],
      "last_updated": "2026-08-18 14:05:11"
    }
  ]
}
```
Each volume lists its attached `hosts` / `host_groups` — the reverse mapping.

## Pagination
Page with `limit` + `offset`; `total` is the unfiltered-by-page count for the
current filter. Example: `/volumes?array_name=purecbs-...&limit=500&offset=0`.

## Errors
| Status | Meaning |
|--------|---------|
| `401`  | Missing or invalid key |
| `403`  | Key lacks the required scope |
| `429`  | Rate limit exceeded (`Retry-After: 60`) |
| `503`  | External API not configured (no keys provisioned) |

## Example

```bash
curl -H "X-API-Key: sip_XXXXXXXX" \
  "http://usodclpsandadm1.corp.intranet:8080/api/ext/v1/volumes?vendor=pure&limit=50"
```

---

## Operator notes (not for partners)

- **Mint a key:** `python backend/scripts/gen_external_key.py <client-name> hosts:read,volumes:read`
  Prints the plaintext (hand off once) and the config entry (hash only).
- **Provision:** append the entry to the `EXTERNAL_API_KEYS` JSON list in `.env`,
  then recreate `usm-backend`. Empty list ⇒ the surface is disabled (503).
- **Revoke:** remove that key's entry and recreate `usm-backend`. Other keys keep working.
- **Rotate:** mint a new key, ship it to the consumer, then remove the old entry.
- Only SHA-256 hashes are stored on disk; the plaintext is never recoverable from config.
- The rate limit is per-key, per-worker (in-process). With 4 API workers the
  effective ceiling is up to `4 × EXTERNAL_API_RATE_LIMIT`; tighten if needed.
