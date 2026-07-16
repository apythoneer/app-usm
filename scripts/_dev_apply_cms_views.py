"""
Apply (or drop) the USM <-> CMS bridge views, then VERIFY them against real data.

    python scripts/_dev_apply_cms_views.py apply     # create/update + verify
    python scripts/_dev_apply_cms_views.py verify    # verify only, no DDL
    python scripts/_dev_apply_cms_views.py drop      # full teardown

Run inside the backend container (it has pyodbc + KeePass creds):
    docker cp scripts/_dev_apply_cms_views.py usm-backend:/tmp/
    docker cp backend/sql usm-backend:/tmp/sql
    docker exec usm-backend python3 /tmp/_dev_apply_cms_views.py apply

Why verification matters more than "did the DDL run":
CREATE VIEW succeeds even when the view will return zero rows — SQL Server does
not evaluate the query at creation time. The documented CMS spec said to filter
ASSIGNMENT='In Use', which for CMSMalApps matches NOTHING (apps use
'Production'). A view built that way creates cleanly and then silently answers
every app question with an empty set. So we assert row counts, not exit codes.
"""
import sys
import pathlib

sys.path.insert(0, "/app")

from app.db.session import get_db_cursor  # noqa: E402

SQL_DIR = pathlib.Path("/tmp/sql")

# view -> (minimum rows we expect, why)
EXPECTED = {
    "vw_cms_app_to_server":            (1000, "spec: 27,593 app<->server matches"),
    "vw_cms_app_to_database":          (1000, "spec: 14,344 app<->db matches"),
    "vw_cms_database_to_server":       (1000, "spec: 22,586 db<->server matches"),
    "vw_cms_app_to_database_to_server": (500, "spec: 21,353 full-chain rows"),
    "vw_cms_server_to_cluster":          (10, "spec: 1,879 server<->cluster"),
    "vw_cms_vm_to_host":               (1000, "spec: 165,660 vm<->host pairs"),
    "vw_cms_server_to_backups":         (100, "spec: 17,335 backup records"),
    "vw_cms_array_to_app_db":           (100, "AllArrayHostAppDBData = 21,273 rows"),
    "vw_cms_switch_to_host_app_db":      (10, "spec: 203 switch rows"),
}


def run_sql_file(path: pathlib.Path) -> None:
    """Execute a .sql file, splitting on GO (pyodbc cannot parse batch separators)."""
    text = path.read_text(encoding="utf-8")
    batches = [b.strip() for b in text.replace("\r\n", "\n").split("\nGO") if b.strip()]
    for i, batch in enumerate(batches, 1):
        # strip a trailing bare GO if present
        batch = batch.rstrip().rstrip("GO").rstrip()
        if not batch:
            continue
        with get_db_cursor() as cur:
            cur.execute(batch)
        print("   batch {}/{} ok".format(i, len(batches)))


def verify() -> int:
    print()
    print("   %-36s %10s  %s" % ("view", "rows", "verdict"))
    print("   " + "-" * 74)
    failures = 0
    for view, (minimum, note) in EXPECTED.items():
        try:
            with get_db_cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM USM.{} WITH (NOLOCK)".format(view))
                n = cur.fetchone()[0]
        except Exception as e:
            print("   %-36s %10s  ERROR: %s" % (view, "-", str(e.args[0])))
            failures += 1
            continue
        ok = n >= minimum
        if not ok:
            failures += 1
        print("   %-36s %10s  %s" % (
            view, format(n, ","),
            "ok" if ok else "FAIL — expected >= {} ({})".format(minimum, note)))
    return failures


def sample() -> None:
    """Prove the chain resolves on real data, the way the spec claimed."""
    print()
    print("   ---- spot-check: full app -> database -> server chain ----")
    with get_db_cursor() as cur:
        cur.execute("""
            SELECT TOP 3 app_acronym, database_name, database_type, server_name, server_os
            FROM USM.vw_cms_app_to_database_to_server WITH (NOLOCK)
            ORDER BY app_acronym
        """)
        for r in cur.fetchall():
            print("      app={:<10} db={:<18} type={:<10} server={:<16} os={}".format(
                *[str(x)[:18] for x in r]))

    print()
    print("   ---- spot-check: CMS array names that match USM arrays ----")
    with get_db_cursor() as cur:
        cur.execute("""
            SELECT TOP 5 c.array_name, COUNT(*) AS rows_
            FROM USM.vw_cms_array_to_app_db c WITH (NOLOCK)
            JOIN USM.metrics_current m WITH (NOLOCK) ON m.array_name = c.array_name
            GROUP BY c.array_name ORDER BY COUNT(*) DESC
        """)
        rows = cur.fetchall()
        if not rows:
            print("      !! no CMS array_name matches USM.metrics_current.array_name")
            print("         (the CMS<->USM join key may need normalising)")
        for a, n in rows:
            print("      {:<28} {} app/db rows".format(a, n))


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "verify"

    if action == "apply":
        print("=== applying USM.vw_cms_* views ===")
        run_sql_file(SQL_DIR / "cms_views.sql")
        rc = verify()
        sample()
        print()
        print("DONE — {}".format("all views verified" if rc == 0 else "{} VIEW(S) FAILED".format(rc)))
        sys.exit(1 if rc else 0)

    elif action == "drop":
        print("=== dropping USM.vw_cms_* views (no data is affected) ===")
        run_sql_file(SQL_DIR / "cms_views_drop.sql")
        print("DONE — views removed; re-create with: apply")

    elif action == "verify":
        rc = verify()
        sample()
        sys.exit(1 if rc else 0)

    else:
        print(__doc__)
        sys.exit(2)
