#!/usr/bin/env python3
"""
usm-docs MCP Server
===================
Serves USM architecture, schema, vendor, and development docs to AI agents
so they can answer questions without re-reading the entire codebase.

Tools:
  - search_docs(query)          : keyword search across all docs/*.md
  - get_doc(name)               : return a full doc by name
  - get_schema(table?)          : DB schema (all tables or one)
  - get_collector_contract()    : BaseCollector interface
  - get_vendor_pattern(vendor)  : example collector code for a vendor
  - get_architecture()          : architecture overview
  - list_docs()                 : list available docs

Run:
  python mcp/usm_docs_server.py
"""

import os
import re
import glob
from mcp.server.fastmcp import FastMCP

# Repo root = parent of this file's directory (mcp/)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(REPO_ROOT, "docs")
COLLECTORS_DIR = os.path.join(REPO_ROOT, "backend", "app", "collectors")

mcp = FastMCP("usm-docs")


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"(error reading {path}: {e})"


def _doc_path(name: str) -> str:
    """Resolve a doc name to a path under docs/ (case-insensitive, .md optional)."""
    name = name.strip()
    if not name.lower().endswith(".md"):
        name += ".md"
    # direct
    direct = os.path.join(DOCS_DIR, name)
    if os.path.exists(direct):
        return direct
    # case-insensitive match
    for p in glob.glob(os.path.join(DOCS_DIR, "*.md")):
        if os.path.basename(p).lower() == name.lower():
            return p
    return ""


@mcp.tool()
def list_docs() -> str:
    """List all available documentation files."""
    files = sorted(glob.glob(os.path.join(DOCS_DIR, "*.md")))
    if not files:
        return "No docs found."
    lines = ["Available docs:"]
    for p in files:
        lines.append(f"  - {os.path.basename(p)}")
    return "\n".join(lines)


@mcp.tool()
def get_doc(name: str) -> str:
    """Return the full contents of a doc by name (e.g. 'ARCHITECTURE' or 'VENDOR_GUIDE.md')."""
    path = _doc_path(name)
    if not path:
        return f"Doc '{name}' not found. Use list_docs to see options."
    return _read(path)


@mcp.tool()
def search_docs(query: str) -> str:
    """Keyword search across all docs. Returns matching sections with context."""
    query_l = query.lower()
    results = []
    for p in sorted(glob.glob(os.path.join(DOCS_DIR, "*.md"))):
        content = _read(p)
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if query_l in line.lower():
                start = max(0, i - 2)
                end = min(len(lines), i + 3)
                snippet = "\n".join(lines[start:end])
                results.append(f"### {os.path.basename(p)} (line {i+1})\n{snippet}\n")
                if len(results) >= 20:
                    break
        if len(results) >= 20:
            break
    if not results:
        return f"No matches for '{query}'."
    return "\n---\n".join(results)


@mcp.tool()
def get_schema(table: str = "") -> str:
    """Return the DB schema. Pass a table name for just that table, or empty for all."""
    contract = _read(_doc_path("COLLECTOR_CONTRACT"))
    # Extract section 3 (Full Database Schema)
    m = re.search(r"## 3\. Full Database Schema.*?(?=\n## 4\.|\Z)", contract, re.S)
    schema_section = m.group(0) if m else contract
    if not table:
        return schema_section
    # find the specific table subsection
    for block in re.split(r"\n### ", schema_section):
        if block.lower().startswith(table.lower()):
            return "### " + block
    return f"Table '{table}' not found in schema. Full schema:\n\n{schema_section}"


@mcp.tool()
def get_collector_contract() -> str:
    """Return the BaseCollector contract and expected data shapes."""
    contract = _read(_doc_path("COLLECTOR_CONTRACT"))
    m = re.search(r"^# .*?(?=## 3\. Full Database Schema)", contract, re.S)
    return m.group(0) if m else contract


@mcp.tool()
def get_vendor_pattern(vendor: str = "") -> str:
    """
    Return example collector code for a vendor. If vendor is given and exists,
    returns that vendor's actual metrics.py. Otherwise returns the template guide.
    """
    if vendor:
        metrics_path = os.path.join(COLLECTORS_DIR, vendor.lower(), "metrics.py")
        if os.path.exists(metrics_path):
            return f"# Actual {vendor} metrics collector\n\n```python\n{_read(metrics_path)}\n```"
    return _read(_doc_path("VENDOR_GUIDE"))


@mcp.tool()
def get_architecture() -> str:
    """Return the full architecture reference."""
    return _read(_doc_path("ARCHITECTURE"))


@mcp.tool()
def list_vendors() -> str:
    """List all vendor collector packages that exist in the codebase."""
    vendors = []
    if os.path.isdir(COLLECTORS_DIR):
        for entry in sorted(os.listdir(COLLECTORS_DIR)):
            full = os.path.join(COLLECTORS_DIR, entry)
            if os.path.isdir(full) and not entry.startswith("__"):
                types = [
                    t for t in ("metrics", "volumes", "alerts")
                    if os.path.exists(os.path.join(full, f"{t}.py"))
                ]
                vendors.append(f"  - {entry}: {', '.join(types) or 'no collectors'}")
    return "Vendor collector packages:\n" + "\n".join(vendors)


if __name__ == "__main__":
    mcp.run()
