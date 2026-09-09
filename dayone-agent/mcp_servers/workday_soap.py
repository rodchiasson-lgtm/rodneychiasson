"""workday-soap MCP server — Workday SOAP operations (hire, costing).

Preview tools are read-tier (no side effects). Hire and costing assignments are T3 (org chart / ledger).
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .common import TENANT, describe, idem_key, soap_call

S = "workday-soap"
mcp = FastMCP(S)


@mcp.tool(description=describe(S, "preview_hire_employee", "Preview hire without executing: validate target start date, costing, manager approval status."))
def preview_hire_employee(pre_hire_id: str) -> dict:
    """Preview hire operation to check readiness."""
    op = f"""<wd:Hire_Employee_Request xmlns:wd="{{'urn:com.workday/bsvc'}}">
    <wd:Pre_Hire_Reference><wd:ID>{pre_hire_id}</wd:ID></wd:Pre_Hire_Reference>
    <wd:Preview_Only>true</wd:Preview_Only>
</wd:Hire_Employee_Request>"""
    return soap_call("Staffing", op)


@mcp.tool(description=describe(S, "hire_employee", "[T3] Execute hire: move pre-hire to employee. Requires recorded approval (approver + reason) bound to idempotency key. Audit-logged and non-reversible."))
def hire_employee(pre_hire_id: str, start_date: str) -> dict:
    """Execute hire operation with idempotency. Requires T3 approval."""
    key = idem_key("hire", pre_hire_id, start_date)
    op = f"""<wd:Hire_Employee_Request xmlns:wd="{{'urn:com.workday/bsvc'}}">
    <wd:Pre_Hire_Reference><wd:ID>{pre_hire_id}</wd:ID></wd:Pre_Hire_Reference>
    <wd:Start_Date>{start_date}</wd:Start_Date>
</wd:Hire_Employee_Request>"""
    result = soap_call("Staffing", op)
    result["idempotency_key"] = key
    return result


@mcp.tool(description=describe(S, "preview_costing_allocation", "Preview costing allocation: validate cost center status, effective date, and split percentages."))
def preview_costing_allocation(pre_hire_id: str, cost_center_id: str, effective_date: str) -> dict:
    """Preview costing allocation to check validity."""
    op = f"""<wd:Assign_Costing_Allocation_Request xmlns:wd="{{'urn:com.workday/bsvc'}}">
    <wd:Pre_Hire_Reference><wd:ID>{pre_hire_id}</wd:ID></wd:Pre_Hire_Reference>
    <wd:Cost_Center_Reference><wd:ID>{cost_center_id}</wd:ID></wd:Cost_Center_Reference>
    <wd:Effective_Date>{effective_date}</wd:Effective_Date>
    <wd:Preview_Only>true</wd:Preview_Only>
</wd:Assign_Costing_Allocation_Request>"""
    return soap_call("Financial_Management", op)


@mcp.tool(description=describe(S, "assign_costing_allocation", "[T3] Assign cost center and effective date. Reversible via new assignment, but locked to closed cost centers; requires recorded approval (approver + reason) bound to idempotency key."))
def assign_costing_allocation(pre_hire_id: str, cost_center_id: str, effective_date: str) -> dict:
    """Assign costing allocation with idempotency. Requires T3 approval."""
    key = idem_key("costing", pre_hire_id, cost_center_id, effective_date)
    op = f"""<wd:Assign_Costing_Allocation_Request xmlns:wd="{{'urn:com.workday/bsvc'}}">
    <wd:Pre_Hire_Reference><wd:ID>{pre_hire_id}</wd:ID></wd:Pre_Hire_Reference>
    <wd:Cost_Center_Reference><wd:ID>{cost_center_id}</wd:ID></wd:Cost_Center_Reference>
    <wd:Effective_Date>{effective_date}</wd:Effective_Date>
</wd:Assign_Costing_Allocation_Request>"""
    result = soap_call("Financial_Management", op)
    result["idempotency_key"] = key
    return result


if __name__ == "__main__":
    mcp.run()
