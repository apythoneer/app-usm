"""Disable non-ONTAP NetApp arrays to prevent KeePass flooding."""
from app.db.session import get_db_cursor
SCHEMA = "USM"

with get_db_cursor() as cur:
    # Disable StorageGrid, CVO, FAS8060 — only keep AFF-A400 enabled
    cur.execute(
        f"UPDATE {SCHEMA}.managed_arrays SET enabled=0, monitoring_status='no_collector' "
        f"WHERE vendor='netapp' AND model NOT LIKE '%AFF-A400%' AND enabled=1"
    )
    print(f"Disabled {cur.rowcount} non-ONTAP NetApp arrays")

    cur.execute(
        f"SELECT model, COUNT(*) as cnt, "
        f"SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END) as on_cnt "
        f"FROM {SCHEMA}.managed_arrays WHERE vendor='netapp' GROUP BY model ORDER BY cnt DESC"
    )
    print("\nNetApp arrays by model:")
    for r in cur.fetchall():
        print(f"  {str(r[0]):20s} total={r[1]:3d}  enabled={r[2]:3d}")
