"""Explore DimStorageFinance — vendor/model breakdown and Pure/NetApp arrays."""
from app.db.session import get_db_cursor

print("=== VENDOR / MODEL BREAKDOWN ===")
with get_db_cursor() as cur:
    cur.execute(
        "SELECT Vendor, Model, Dispostition, COUNT(*) as cnt "
        "FROM dbo.DimStorageFinance "
        "WHERE ActiveRecord = 1 "
        "GROUP BY Vendor, Model, Dispostition "
        "ORDER BY Vendor, cnt DESC"
    )
    for row in cur.fetchall():
        print(f"  {row[0]:20s} {row[1]:30s} {row[2]:15s} count={row[3]}")

print("\n=== PURE STORAGE ARRAYS ===")
with get_db_cursor() as cur:
    cur.execute(
        "SELECT ArrayNameKey, VendorArrayName, ArrayFQDN, Model, Site, Category, Usage, Dispostition "
        "FROM dbo.DimStorageFinance "
        "WHERE Vendor LIKE '%Pure%' AND ActiveRecord = 1 "
        "ORDER BY Site"
    )
    for row in cur.fetchall():
        print(f"  {row[0]:35s} FQDN={row[2]:50s} Model={row[3]:20s} Site={row[4]:6s} Cat={row[5]:10s} Usage={row[6]:10s}")

print("\n=== NETAPP ARRAYS ===")
with get_db_cursor() as cur:
    cur.execute(
        "SELECT ArrayNameKey, VendorArrayName, ArrayFQDN, Model, Site, Category, Usage, Dispostition "
        "FROM dbo.DimStorageFinance "
        "WHERE Vendor LIKE '%NetApp%' AND ActiveRecord = 1 "
        "ORDER BY Site"
    )
    for row in cur.fetchall():
        print(f"  {row[0]:35s} FQDN={row[2]:50s} Model={row[3]:20s} Site={row[4]:6s}")

print("\n=== SITE BREAKDOWN (Active) ===")
with get_db_cursor() as cur:
    cur.execute(
        "SELECT Site, COUNT(*) as cnt "
        "FROM dbo.DimStorageFinance "
        "WHERE ActiveRecord = 1 "
        "GROUP BY Site ORDER BY cnt DESC"
    )
    for row in cur.fetchall():
        print(f"  {row[0]:10s} {row[1]} arrays")

print("\n=== TECHNOLOGY TYPES (Active) ===")
with get_db_cursor() as cur:
    cur.execute(
        "SELECT Technology, Category, COUNT(*) as cnt "
        "FROM dbo.DimStorageFinance "
        "WHERE ActiveRecord = 1 "
        "GROUP BY Technology, Category ORDER BY cnt DESC"
    )
    for row in cur.fetchall():
        print(f"  {row[0]:15s} {row[1]:15s} {row[2]}")
