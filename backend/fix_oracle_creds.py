"""Update Oracle managed_arrays entries with ZFS_root cred_key and enable them."""
from app.db.session import get_db_cursor
SCHEMA = "USM"

with get_db_cursor() as cur:
    cur.execute(
        f"UPDATE {SCHEMA}.managed_arrays "
        f"SET cred_key = 'ZFS_root', enabled = 1, monitoring_status = 'active' "
        f"WHERE vendor = 'oracle' AND disposition = 'Current'"
    )
    print(f"Updated {cur.rowcount} Oracle arrays with cred_key=ZFS_root")
