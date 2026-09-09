#!/usr/bin/env python3
"""PreToolUse gate hook for the DayOne agent's Workday MCP tools.

Reads policy/tiers.yaml and enforces, per call:

  read  execute freely
  T1    execute, append an audit-log entry
  T2    ask the operator to confirm before executing
  T3    ask the operator to confirm, naming the approvers on record for that
        stage; the operator's approver name + reason are captured in the
        confirmation, not in the tool arguments

kill_switch: true in tiers.yaml drops every non-read tier to a hard deny.

Claude Code invokes this as: python .claude/hooks/gate.py
with the PreToolUse event as a JSON object on stdin, and reads a JSON object
back from stdout (see the "Hooks" section of the Claude Code docs for the
hookSpecificOutput / permissionDecision contract this implements).
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
TIERS_PATH = ROOT / "policy" / "tiers.yaml"
AUDIT_LOG = ROOT / ".claude" / "audit.log"

MANAGED_SERVERS = {"workday-hcm", "workday-soap", "workday-data", "policy-docs"}


def load_policy() -> dict:
    return yaml.safe_load(TIERS_PATH.read_text())


def parse_tool_name(tool_name: str) -> tuple[str, str] | None:
    """'mcp__workday-hcm__hire_employee' -> ('workday-hcm', 'hire_employee')."""
    if not tool_name.startswith("mcp__"):
        return None
    rest = tool_name[len("mcp__"):]
    server, _, tool = rest.partition("__")
    if not tool or server not in MANAGED_SERVERS:
        return None
    return server, tool


def stage_for(tool: str) -> str | None:
    lowered = tool.lower()
    if "hire" in lowered:
        return "hire"
    if "costing" in lowered:
        return "costing"
    return None


def append_audit(entry: dict) -> None:
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOG.open("a") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


def decision(permission: str, reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": permission,
            "permissionDecisionReason": reason,
        }
    }


def main() -> None:
    event = json.load(sys.stdin)
    tool_name = event.get("tool_name", "")

    parsed = parse_tool_name(tool_name)
    if parsed is None:
        # Not one of our four MCP servers - nothing to gate.
        print(json.dumps({}))
        return

    server, tool = parsed
    policy = load_policy()
    tier = policy.get("tools", {}).get(server, {}).get(tool, "T3")
    kill_switch = policy.get("kill_switch", False)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    if kill_switch and tier != "read":
        append_audit({"timestamp": now, "server": server, "tool": tool, "tier": tier, "status": "denied", "reason": "kill_switch"})
        print(json.dumps(decision("deny", f"[{tier}] {server}:{tool} blocked - kill switch is engaged, all writes are read-only.")))
        return

    if tier == "read":
        print(json.dumps({}))
        return

    if tier == "T1":
        append_audit({"timestamp": now, "server": server, "tool": tool, "tier": tier, "status": "auto-approved"})
        print(json.dumps(decision("allow", f"[{tier}] {server}:{tool} is reversible and low-consequence - executing and logging.")))
        return

    if tier == "T2":
        append_audit({"timestamp": now, "server": server, "tool": tool, "tier": tier, "status": "pending_confirmation"})
        print(json.dumps(decision("ask", f"[{tier}] {server}:{tool} creates work for someone else (e.g. IT provisioning). Confirm to proceed.")))
        return

    # T3 (and any tool missing from the tier registry, which defaults to T3)
    stage = stage_for(tool)
    approvers = policy.get("approval_matrix", {}).get(stage, []) if stage else []
    approver_list = ", ".join(approvers) if approvers else "an approver named in policy/tiers.yaml"
    append_audit({"timestamp": now, "server": server, "tool": tool, "tier": tier, "status": "pending_confirmation", "stage": stage, "eligible_approvers": approvers})
    print(json.dumps(decision(
        "ask",
        f"[T3] {server}:{tool} touches the org chart or the ledger and is non-reversible. "
        f"Requires a recorded approval: name the approver ({approver_list}) and the reason before confirming.",
    )))


if __name__ == "__main__":
    main()
