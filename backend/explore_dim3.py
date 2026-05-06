"""Explore Pure/NetApp arrays in DimStorageFinance."""
from app.db.session import get_db_cursor

print("=== PURE STORAGE ARRAYS (Active) ===")
with get_db_cursor() as cur:
    cur.execute(
        "SELECT ArrayNameKey, ArrayFQDN, Model, Site, Category, Usage "
        "FROM dbo.DimStorageFinance "
        "WHERE Vendor LIKE '%Pure%' AND ActiveRecord = 1 "
        "ORDER BY Site, Model"
    )
    for row in cur.fetchall():
        print(f"  {str(row[0] or ''):35s} FQDN={str(row[1] or ''):50s} Model={str(row[2] or ''):20s} Site={str(row[3] or ''):6s} Cat={str(row[4] or ''):10s} Use={str(row[5] or '')}")

print(f"\n=== NETAPP ARRAYS (Active) ===")
with get_db_cursor() as cur:
    cur.execute(
        "SELECT ArrayNameKey, ArrayFQDN, Model, Site, Category, Usage "
        "FROM dbo.DimStorageFinance "
        "WHERE Vendor LIKE '%NetApp%' AND ActiveRecord = 1 "
        "ORDER BY Model, Site"
    )
    for row in cur.fetchall():
        print(f"  {str(row[0] or ''):35s} FQDN={str(row[1] or ''):50s} Model={str(row[2] or ''):20s} Site={str(row[3] or ''):6s}")

print(f"\n=== SUMMARY BY VENDOR (Active, Current disposition) ===")
with get_db_cursor() as cur:
    cur.execute(
        "SELECT Vendor, COUNT(*) as cnt "
        "FROM dbo.DimStorageFinance "
        "WHERE ActiveRecord = 1 AND Dispostition = 'Current' "
        "GROUP BY Vendor ORDER BY cnt DESC"
    )
    for row in cur.fetchall():
        print(f"  {str(row[0] or ''):20s} {row[1]} arrays")
