"""
Reset managed_arrays: delete all entries, then re-sync from DimStorageFinance.
This ensures ALL arrays have consistent metadata from the authoritative source.
Manual entries (pre-DimStorageFinance) will be replaced with properly formatted ones.
"""
from app.db.session import get_db_cursor
from app.services.inventory import sync_from_dim_storage_finance
from app.core.config import get_settings

settings = get_settings()
SCHEMA = settings.db_schema

# Step 1: Count current arrays
with get_db_cursor() as cur:
    cur.execute(f"SELECT COUNT(*) FROM {SCHEMA}.managed_arrays")
    before = cur.fetchone()[0]
    print(f"Current managed_arrays count: {before}")

# Step 2: Delete ALL managed_arrays
with get_db_cursor() as cur:
    cur.execute(f"DELETE FROM {SCHEMA}.managed_arrays")
    print(f"Deleted {cur.rowcount} rows from managed_arrays")

# Step 3: Re-sync from DimStorageFinance
print("\nRe-syncing from DimStorageFinance...")
stats = sync_from_dim_storage_finance()
print(f"\nSync results: {stats}")

# Step 4: Re-apply HPE cred_keys (all HPE use HPE_sanadmin)
with get_db_cursor() as cur:
    cur.execute(
        f"UPDATE {SCHEMA}.managed_arrays "
        f"SET cred_key = 'HPE_sanadmin', enabled = 1, monitoring_status = 'active' "
        f"WHERE vendor = 'hpe' AND disposition = 'Current'"
    )
    print(f"\nRe-applied HPE_sanadmin cred_key to {cur.rowcount} HPE arrays")

# Step 5: Verify final state
with get_db_cursor() as cur:
    cur.execute(
        f"SELECT vendor, COUNT(*) as total, "
        f"SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END) as enabled, "
        f"SUM(CASE WHEN dim_sync_at IS NOT NULL THEN 1 ELSE 0 END) as synced "
        f"FROM {SCHEMA}.managed_arrays GROUP BY vendor ORDER BY total DESC"
    )
    print(f"\nFinal state:")
    for row in cur.fetchall():
        print(f"  {row[0]:15s} total={row[1]:4d}  enabled={row[2]:4d}  synced={row[3]:4d}")

    cur.execute(f"SELECT COUNT(*) FROM {SCHEMA}.managed_arrays")
    total = cur.fetchone()[0]
    print(f"\nTotal: {total} arrays (all with consistent DimStorageFinance metadata)")
