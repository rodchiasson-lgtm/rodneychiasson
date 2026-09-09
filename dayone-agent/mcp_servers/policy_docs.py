"""policy-docs MCP server — Workday hiring and finance policies (read-only).

Read-tier tools providing access to the tier registry and onboarding rules.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from mcp.server.fastmcp import FastMCP

from .common import describe

S = "policy-docs"
mcp = FastMCP(S)

ROOT = Path(__file__).resolve().parent.parent
POLICIES = yaml.safe_load((ROOT / "policy" / "tiers.yaml").read_text())


@mcp.tool(description=describe(S, "list_policies", "List all policy documents: tier registry, approval matrix by stage, and kill-switch status."))
def list_policies() -> dict:
    """Return the complete policy registry."""
    return {
        "policies": {
            "tiers": {
                "description": "Risk tier registry (read, T1, T2, T3) for all MCP tools",
                "tools": POLICIES.get("tools", {}),
            },
            "approval_matrix": {
                "description": "Named approvers for T3 writes by stage (hire, costing)",
                "stages": POLICIES.get("approval_matrix", {}),
            },
            "kill_switch": {
                "description": "Global read-only override: when true, all tiers drop to read-only",
                "enabled": POLICIES.get("kill_switch", False),
            },
        }
    }


@mcp.tool(description=describe(S, "lookup_policy", "Look up a specific tool's tier, approvers for its stage, and rationale."))
def lookup_policy(server: str, tool: str) -> dict:
    """Look up a specific tool's tier and approval requirements."""
    tier = POLICIES.get("tools", {}).get(server, {}).get(tool, "T3")  # unknown tools are T3
    if tier == "read":
        return {"tool": f"{server}:{tool}", "tier": "read", "approvers": None}

    # Find the stage for this tool (hack: infer from common patterns)
    stage = None
    if "hire" in tool.lower():
        stage = "hire"
    elif "costing" in tool.lower():
        stage = "costing"
    else:
        return {"tool": f"{server}:{tool}", "tier": tier, "approvers": None}

    approvers = POLICIES.get("approval_matrix", {}).get(stage, [])
    return {
        "tool": f"{server}:{tool}",
        "tier": tier,
        "stage": stage,
        "approvers": approvers,
        "kill_switch_enabled": POLICIES.get("kill_switch", False),
    }


if __name__ == "__main__":
    mcp.run()
