"""Explore DimStorageFinance table structure and sample data."""
from app.db.session import get_db_cursor

print("=== COLUMNS ===")
with get_db_cursor() as cur:
    cur.execute(
        "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH "
        "FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_NAME='DimStorageFinance' "
        "ORDER BY ORDINAL_POSITION"
    )
    for row in cur.fetchall():
        print(f"  {row[0]:40s} {str(row[1]):15s} {row[2]}")

print("\n=== ROW COUNT ===")
with get_db_cursor() as cur:
    cur.execute("SELECT COUNT(*) FROM dbo.DimStorageFinance")
    print(f"  Total rows: {cur.fetchone()[0]}")

print("\n=== SAMPLE (5 rows) ===")
with get_db_cursor() as cur:
    cur.execute("SELECT TOP 5 * FROM dbo.DimStorageFinance")
    cols = [c[0] for c in cur.description]
    print(f"  Columns: {cols}")
    for row in cur.fetchall():
        print(f"  {dict(zip(cols, row))}")

print("\n=== DISTINCT VENDORS/TYPES ===")
with get_db_cursor() as cur:
    try:
        cur.execute(
            "SELECT DISTINCT StorageVendor, StorageModel, COUNT(*) as cnt "
            "FROM dbo.DimStorageFinance "
            "GROUP BY StorageVendor, StorageModel "
            "ORDER BY cnt DESC"
        )
        for row in cur.fetchall():
            print(f"  {row[0]:20s} {row[1]:30s} count={row[2]}")
    except Exception as e:
        print(f"  Could not query vendor/model: {e}")
        # Try to find column names that might indicate vendor
        cur.execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME='DimStorageFinance' "
            "AND COLUMN_NAME LIKE '%vendor%' OR COLUMN_NAME LIKE '%type%' "
            "OR COLUMN_NAME LIKE '%model%' OR COLUMN_NAME LIKE '%brand%'"
        )
        print(f"  Matching columns: {[r[0] for r in cur.fetchall()]}")
