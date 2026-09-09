"""workday-hcm MCP server — Workday REST (staffing, recruiting, financial management) + ITSM.

Reads are free. update_requisition_target_date is T1. create_provisioning_request is T2.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .common import TENANT, describe, idem_key, rest_get, rest_write

S = "workday-hcm"
mcp = FastMCP(S)
API = f"/ccx/api/v1/{TENANT}"


@mcp.tool(description=describe(S, "get_position", "Live position record: job profile, supervisory org, manager, availability date, hiring-freeze flag, default cost center."))
def get_position(position_id: str) -> dict:
    return rest_get(f"{API}/staffing/v1/positions/{position_id}")


@mcp.tool(description=describe(S, "get_requisition", "Job requisition status, target hire date and linked candidate."))
def get_requisition(requisition_id: str) -> dict:
    return rest_get(f"{API}/recruiting/v1/jobRequisitions/{requisition_id}")


@mcp.tool(description=describe(S, "get_pre_hire", "Pre-hire record: confirmed start date, hire business-process status and awaiting step, worker ID once hired, current costing allocation. PII fields are stripped."))
def get_pre_hire(pre_hire_id: str) -> dict:
    return rest_get(f"{API}/staffing/v1/preHires/{pre_hire_id}")


@mcp.tool(description=describe(S, "list_onboarding_tasks", "Workday onboarding checklist tasks (manager welcome note, buddy assigned, ...) for a pre-hire."))
def list_onboarding_tasks(pre_hire_id: str) -> dict:
    return rest_get(f"{API}/staffing/v1/preHires/{pre_hire_id}/onboardingTasks")


@mcp.tool(description=describe(S, "get_cost_center", "Cost center master record: name, status (Active/Closed), successor if closed."))
def get_cost_center(cost_center_id: str) -> dict:
    return rest_get(f"{API}/financialManagement/v1/costCenters/{cost_center_id}")


@mcp.tool(description=describe(S, "list_provisioning_tickets", "ITSM provisioning tickets (laptop, identity, badge) for a pre-hire, from the external service-management system."))
def list_provisioning_tickets(pre_hire_id: str) -> dict:
    return rest_get("/itsm/tickets", pre_hire=pre_hire_id)


@mcp.tool(description=describe(S, "update_requisition_target_date", "Align a requisition's target hire date with the confirmed start date. Reversible metadata change; executes automatically and is logged."))
def update_requisition_target_date(requisition_id: str, target_hire_date: str) -> dict:
    key = idem_key("requisition.target", requisition_id, target_hire_date)
    return rest_write("PATCH", f"{API}/recruiting/v1/jobRequisitions/{requisition_id}", {"targetHireDate": target_hire_date}, key)


@mcp.tool(description=describe(S, "create_provisioning_request", "Open ITSM provisioning tickets for a pre-hire (types: laptop, identity, badge). Creates work for IT — the operator will be asked to confirm before this runs. Always cite the onboarding policy paragraph that requires it."))
def create_provisioning_request(pre_hire_id: str, types: list[str], due_date: str, policy_citation: str) -> dict:
    key = idem_key("itsm.create", pre_hire_id, sorted(types), due_date)
    return rest_write("POST", "/itsm/tickets", {"pre_hire": pre_hire_id, "types": types, "due": due_date, "citation": policy_citation}, key)


if __name__ == "__main__":
    mcp.run()
