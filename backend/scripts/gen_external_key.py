#!/usr/bin/env python3
"""
Mint an API key for the external (partner) read-only API.

Usage:
    python backend/scripts/gen_external_key.py <client-name> [scopes]

    scopes: comma-separated, default "hosts:read,volumes:read"

Prints:
  1. the PLAINTEXT key — hand this to the consumer ONCE; it is never recoverable.
  2. the config entry (name + sha256 + scopes) to add to EXTERNAL_API_KEYS.

Only the hash is stored server-side, so a leaked .env cannot call the API.
"""
import hashlib
import json
import secrets
import sys


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "client"
    scopes = (
        sys.argv[2].split(",")
        if len(sys.argv) > 2
        else ["hosts:read", "volumes:read"]
    )
    token = "sip_" + secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    entry = {"name": name, "key_sha256": digest, "scopes": scopes}

    print("=" * 68)
    print("PLAINTEXT KEY  (give to the consumer once, over a secure channel):")
    print(f"  {token}")
    print("-" * 68)
    print("CONFIG ENTRY  (append to the EXTERNAL_API_KEYS JSON list in .env):")
    print(f"  {json.dumps(entry)}")
    print("=" * 68)


if __name__ == "__main__":
    main()
