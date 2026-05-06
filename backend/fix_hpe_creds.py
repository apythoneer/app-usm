"""Update HPE managed_arrays entries with correct cred_keys and enable them."""
from app.db.session import get_db_cursor
from app.core.config import get_settings

settings = get_settings()
SCHEMA = settings.db_schema

# All HPE arrays should use HPE_sanadmin credential key
# (verified working on 3Par 20450)
with get_db_cursor() as cur:
    cur.execute(
        f"UPDATE {SCHEMA}.managed_arrays "
        f"SET cred_key = 'HPE_sanadmin', enabled = 1, "
        f"monitoring_status = 'active', updated_at = GETDATE() "
        f"WHERE vendor = 'hpe' AND disposition = 'Current'"
    )
    print(f"Updated {cur.rowcount} HPE arrays with cred_key=HPE_sanadmin")

    # Verify
    cur.execute(
        f"SELECT array_name, cred_key, enabled, model, array_fqdn "
        f"FROM {SCHEMA}.managed_arrays WHERE vendor = 'hpe' AND enabled = 1"
    )
    for row in cur.fetchall():
        print(f"  {row[0]:25s} key={row[1]:20s} model={row[3]:20s} fqdn={row[4]}")
