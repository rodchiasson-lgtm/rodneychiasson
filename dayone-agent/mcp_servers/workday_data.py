"""workday-data MCP server — Workday Prism analytics queries (readiness dataset).

Read-tier tools querying the DuckDB in-memory hire_readiness table built by the mock tenant.
"""
from __future__ import annotations

import httpx
from mcp.server.fastmcp import FastMCP

from .common import BASE, REST_TOKEN, TENANT, describe, strip_pii

S = "workday-data"
mcp = FastMCP(S)


@mcp.tool(description=describe(S, "list_pending_hires", "Pending hires starting within the readiness window (next 14 days): pre-hire ID, name, org, position, start date, blocker count, and hire event status."))
def list_pending_hires() -> dict:
    """Query the Prism hire_readiness dataset for pending hires in the readiness window."""
    url = f"{BASE}/ccx/api/prismAnalytics/v3/{TENANT}/datasets/hire_readiness"
    try:
        with httpx.Client(timeout=15) as c:
            r = c.get(url, headers={"Authorization": f"Bearer {REST_TOKEN}"}, params={"days": 14})
    except httpx.TimeoutException:
        return {"error": "timeout", "retryable": True, "table": "hire_readiness"}
    if r.status_code >= 400:
        return {"error": f"HTTP {r.status_code}", "detail": r.text[:400], "url": url}
    try:
        result = r.json()
        return {"rows": result.get("data", [])}
    except Exception as e:
        return {"error": str(e), "detail": r.text[:400]}


@mcp.tool(description=describe(S, "get_readiness_row", "Deep-dive readiness snapshot for one pre-hire: all 5 stages (Position, Requisition, Hire, Provisioning, Costing), blockers, and recommended remediation steps."))
def get_readiness_row(pre_hire_id: str) -> dict:
    """Query detailed readiness state for a single pre-hire."""
    url = f"{BASE}/prism/readiness/{pre_hire_id}"
    try:
        with httpx.Client(timeout=15) as c:
            r = c.get(url, headers={"Authorization": f"Bearer {REST_TOKEN}"})
    except httpx.TimeoutException:
        return {"error": "timeout", "retryable": True, "pre_hire": pre_hire_id}
    if r.status_code >= 400:
        return {"error": f"HTTP {r.status_code}", "detail": r.text[:400], "url": url, "pre_hire": pre_hire_id}
    try:
        return strip_pii(r.json())
    except Exception as e:
        return {"error": str(e), "detail": r.text[:400]}


if __name__ == "__main__":
    mcp.run()
