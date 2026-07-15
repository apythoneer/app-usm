"""
KeePass REST API — containerized version with caching and gunicorn support.
Serves credentials from a KeePass .kdbx database file over HTTP.

Improvements over the original:
- Response caching (60s) to reduce DB file reads
- Gunicorn with 4 workers for concurrency
- Better error handling
- Health check endpoint
"""

import os
import time
import logging
import threading
from flask import Flask
from flask_restful import Resource, Api
from pykeepass import PyKeePass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("keepass")

# Configuration from environment variables
KEEPASS_DB = os.environ.get("KEEPASS_DB", "/data/StorageOps-Database.kdbx")
CACHE_TTL = int(os.environ.get("CACHE_TTL", "60"))  # seconds

# The vault master password is required and has no default. It previously carried a
# hardcoded fallback, which meant the master password for every array/SQL credential
# was committed to source control. Fail loudly rather than silently unlocking the
# vault with a known-compromised value.
KEEPASS_PASSWORD = os.environ.get("KEEPASS_PASSWORD")
if not KEEPASS_PASSWORD:
    raise RuntimeError(
        "KEEPASS_PASSWORD is not set. Provide it via the environment "
        "(see .env.example) — there is no default."
    )

# In-memory cache
_cache = {}
_cache_lock = threading.Lock()
_cache_ts = 0


def _get_kp():
    """Open the KeePass database. Called per-request (gunicorn workers are separate)."""
    return PyKeePass(KEEPASS_DB, password=KEEPASS_PASSWORD)


def _get_all_entries():
    """Get all entries with caching."""
    global _cache, _cache_ts
    now = time.time()
    with _cache_lock:
        if _cache and (now - _cache_ts) < CACHE_TTL:
            return _cache

    try:
        kp = _get_kp()
        result = {}
        for entry in kp.entries:
            grp = str(entry.group).split(': ')[-1].replace('"', '')
            if grp not in result:
                result[grp] = []
            result[grp].append(entry.title)

        with _cache_lock:
            _cache = result
            _cache_ts = now
        return result
    except Exception as e:
        logger.error(f"Failed to read KeePass DB: {e}")
        with _cache_lock:
            return _cache  # return stale cache on error


class KeePassPull(Resource):
    def get(self, title_id):
        try:
            kp = _get_kp()
            kpentry = kp.find_entries(title=title_id, first=True)
            if kpentry:
                return {"Username": kpentry.username, "Password": kpentry.password}
            return "Invalid Entry. Please Try again... ", 404
        except Exception as e:
            logger.error(f"Error fetching entry '{title_id}': {e}")
            return {"error": str(e)}, 500


class MainClass(Resource):
    def get(self):
        return _get_all_entries()


class HealthCheck(Resource):
    def get(self):
        try:
            kp = _get_kp()
            count = len(kp.entries)
            return {"status": "healthy", "entries": count, "db": KEEPASS_DB}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}, 503


# Flask app setup (used by gunicorn)
app = Flask(__name__)
api = Api(app)
api.add_resource(KeePassPull, '/keepass/<string:title_id>')
api.add_resource(MainClass, '/')
api.add_resource(HealthCheck, '/health')

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=2000, debug=False)
