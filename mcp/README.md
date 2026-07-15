# USM MCP Servers

Three [Model Context Protocol](https://modelcontextprotocol.io) servers that make AI-assisted development of USM faster and more reliable. They let an AI agent (Cline) understand the project, scaffold new code, and inspect the live system **without re-reading the whole codebase every session**.

> **Why this exists:** So that after any machine/OS change, you only need to re-run the *Setup* steps below — you never have to re-explain the project to the AI again. The knowledge lives in `docs/` and is served by `usm-docs`.

---

## The three servers

| Server | What it does | When the AI uses it |
|--------|--------------|---------------------|
| **usm-docs** | Serves `docs/*.md` (architecture, schema, vendor guide, dev guide) as queryable tools. | "How does X work?", "What's the schema for table Y?", "Show me the collector pattern." |
| **usm-dev** | Scaffolds new vendor collectors, validates them against the contract, lints SQL table refs, checks env vars, lists endpoints. | "Add a new vendor", "Is my collector correct?", "What endpoints exist?" |
| **usm-ops** | Talks to a **running** USM backend over HTTP — fleet status, scheduler jobs, alerts, Teams test. | "Is the fleet healthy?", "Run the pure_metrics job", "Show critical alerts." |

### Tool reference

**usm-docs**
- `list_docs()`, `get_doc(name)`, `search_docs(query)`
- `get_schema(table?)`, `get_collector_contract()`, `get_architecture()`
- `get_vendor_pattern(vendor?)`, `list_vendors()`

**usm-dev**
- `scaffold_vendor(vendor, api_type)` — creates the 4 collector files
- `validate_collector(vendor)` — checks contract conformance
- `lint_schema()` — flags SQL table refs not in the known schema
- `check_env()` — lists config.py env vars and whether they're set
- `list_endpoints()` — lists all FastAPI v1 routes

**usm-ops** (needs a reachable backend; set `USM_API_BASE`)
- `health()`, `fleet_status()`, `list_arrays(vendor?, status?)`, `array_detail(name)`
- `scheduler_status()`, `run_job(job_id)`, `recent_alerts(severity?, limit?)`, `test_teams_alert()`

---

## Setup (run this after any new machine / OS reinstall)

### 1. Install dependencies
```bash
cd "c:\Users\AD64490\OneDrive - Lumen\Desktop\unified_storage_monitoring\mcp"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Register the servers with Cline
Open the Cline MCP settings file:
`%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json`

Merge in the contents of [`cline_mcp_settings.example.json`](cline_mcp_settings.example.json).
**Update the absolute paths** if the repo location changed, and update `USM_API_BASE` if the backend host changed.

> If you used the venv, point `"command"` at `.venv\Scripts\python.exe` instead of `python`.

### 3. Reload Cline
Reload the VS Code window (`Ctrl+Shift+P` → "Reload Window"). The three servers should appear connected in Cline's MCP panel.

### 4. Verify
Ask Cline things like:
- "Use usm-docs to show the database schema."
- "Use usm-dev to list the API endpoints."
- "Use usm-ops to check fleet status." (requires the backend running & reachable)

---

## Notes
- `usm-docs` and `usm-dev` work fully offline against the local repo files.
- `usm-ops` requires network access to the running backend (`USM_API_BASE`). On the corporate network the default host resolves; off-network it will return connection errors (expected).
- All servers are pure-Python stdio MCP servers — no extra services to run; Cline launches them on demand.
- To extend: add a new `@mcp.tool()` function to the relevant server and reload.
