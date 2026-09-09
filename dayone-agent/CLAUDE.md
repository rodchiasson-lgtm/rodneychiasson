# DayOne Agent: Workday HR/Finance Operations

An interactive agent for showcasing Workday HR and Finance operations expertise. Built on Claude with MCP integration, tier-based approval gating, and audit logging.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ Claude Code Agent                                               │
│  • Queries Workday REST/SOAP APIs and Prism analytics          │
│  • Enforces tier-based approval gating (read, T1, T2, T3)      │
│  • Logs all operations to audit trail                           │
│  • Interactive natural language + structured tool dispatch      │
└────────────┬────────────────────────────────────────────────────┘
             │ MCP (Model Context Protocol)
             │
     ┌───────┴───────────────────────────────────────────────┐
     │                                                       │
┌────▼──────────┐  ┌───────────────┐  ┌──────────────────┐ │
│ workday-hcm   │  │ workday-soap  │  │  workday-data    │ │
│ (8 tools)     │  │ (4 tools)     │  │  (2 tools)       │ │
│               │  │               │  │                  │ │
│ • Staffing    │  │ • Preview hire│  │ • Pending hires  │ │
│ • Recruiting  │  │ • Hire        │  │ • Deep readiness │ │
│ • Financials  │  │ • Preview     │  │                  │ │
│ • ITSM        │  │   costing     │  │ [Prism queries]  │ │
│               │  │ • Assign      │  │                  │ │
│               │  │   costing     │  │                  │ │
└────┬──────────┘  └───────┬───────┘  └────────┬─────────┘ │
     │                    │                   │            │
     └────────────────────┼───────────────────┼────────────┘
                          │
                          │
          ┌───────────────┴────────────────────┐
          │ FastAPI Mock Workday Tenant        │
          │ (http://127.0.0.1:8765)            │
          │                                    │
          │ • DuckDB in-memory Prism dataset   │
          │ • Seed data: 5 pre-hires w/ faults │
          │ • REST + SOAP + WS-Security        │
          │ • Idempotent writes                │
          │ • PII stripping                    │
          └────────────────────────────────────┘
```

## Five-Stage New-Hire Readiness Workflow

Every pre-hire flows through five stages to onboarding completion:

1. **Position** — Job profile, supervisory org, hiring freeze status
2. **Requisition** — Target hire date alignment with confirmed start date
3. **Hire Event** — HR business process (validation, manager approval, execution)
4. **Provisioning** — ITSM tickets (laptop, identity, badge)
5. **Costing Allocation** — Cost center assignment, effective date

Each stage has blockers (blocked, not_started, pending) and target states (completed, awaiting_step).

## Risk Tiers & Approval

| Tier | Description | Example | Gate Behavior |
|------|-------------|---------|---------------|
| read | No side effects | get_position, list_pending_hires | ✓ Execute freely |
| T1 | Reversible, low consequence | update_requisition_target_date | ✓ Execute + log |
| T2 | Creates work for someone else | create_provisioning_request | ⚠️ Ask operator to confirm |
| T3 | Org chart or ledger impact | hire_employee, assign_costing_allocation | 🔒 Requires recorded approval |

**T3 Approval Requirements:**
- Named approver from approval_matrix (stage-specific)
- Reason text
- Idempotency key (SHA256 hash of canonical args)
- All three bound together in audit log

## Seeded Demo Data

Five pre-hires (PH-9001 to PH-9005), each with max one deliberate fault:

| Pre-Hire | Name | Fault | Blocker | Remediation |
|----------|------|-------|---------|------------|
| PH-9001 | Priya Nair | stale_costing | Costing points to closed CC-4410 | Reassign to CC-4470 (T3) |
| PH-9002 | Jonas Weber | missing_laptop_ticket | No ITSM laptop ticket | Create provisioning request (T2) |
| PH-9003 | Amara Okafor | bp_awaiting_approval | Hire event awaiting manager (Tom Achebe) | Manager approves in Workday |
| PH-9004 | Luca Moretti | frozen_position | Position under hiring freeze | Resolve freeze, then proceed |
| PH-9005 | Hannah Lindqvist | (none) | ✓ Ready to hire | Execute hire_employee (T3) |

## Setup & Running

### 1. Install Dependencies

```bash
cd /home/claude/dayone-agent
python -m pip install -e .
```

### 2. Start Mock Workday Tenant

```bash
# Terminal 1
cd /home/claude/dayone-agent
python -m mock_tenant.app
# Runs on http://127.0.0.1:8765
```

### 3. Start MCP Servers

Claude Code (`claude code`) will start the MCP servers defined in `.mcp.json` automatically.

To run manually:

```bash
# Terminal 2
python -m mcp_servers.workday_hcm

# Terminal 3
python -m mcp_servers.workday_soap

# Terminal 4
python -m mcp_servers.workday_data

# Terminal 5
python -m mcp_servers.policy_docs
```

### 4. Run Claude Code Agent

```bash
claude code
```

The agent will load `.mcp.json`, connect to all four MCP servers, and await natural-language commands.

## Example Interactions

### Scenario 1: Check Readiness (Read-tier)

> **You:** Which pre-hires are ready to hire in the next 14 days?

Agent calls `workday-data:list_pending_hires()` → returns 5 rows with blocker counts.

Agent calls `workday-data:get_readiness_row()` for each → drills into stage state and recommended steps.

### Scenario 2: Fix Stale Costing (T3 Approval)

> **You:** PH-9001 is ready except for stale costing. Reassign to CC-4470 effective today.

Agent:
1. Calls `workday-soap:preview_costing_allocation()` to validate
2. Asks you for approver name, reason, proceeds on your confirmation
3. Calls `workday-soap:assign_costing_allocation()` with T3 approval metadata
4. Logs to audit trail

### Scenario 3: Provision Laptop (T2 Gate)

> **You:** PH-9002 needs an ITSM laptop ticket created by tomorrow.

Agent:
1. Calls `workday-hcm:create_provisioning_request()` with ["laptop"], due date, policy citation
2. Gate returns T2 (creates work for IT team)
3. Operator is asked to confirm before proceeding
4. On confirmation, ticket is created and logged

### Scenario 4: Review Policies

> **You:** Who can approve hire actions? What's the kill switch status?

Agent calls `policy-docs:list_policies()` → returns tier registry, approval matrix, kill-switch flag.

## Audit Trail

All operations logged to `.claude/audit.log` as JSON:

```json
{"timestamp": "2026-09-06T00:00:00Z", "server": "workday-soap", "tool": "hire_employee", "tier": "T3", "idem_key": "dk_abc123...", "approver": "Dana Whitfield", "reason": "PH-9005 ready to start Sep 9", "status": "approved"}
{"timestamp": "2026-09-06T00:00:05Z", "server": "workday-soap", "tool": "hire_employee", "tier": "T3", "idem_key": "dk_abc123...", "status": "deferred"}
```

Approvals are idempotent: same idem_key + different approver = error. Retry with same approver/reason succeeds silently.

## MCP Server Index

| Server | Tools | Tiers |
|--------|-------|-------|
| workday-hcm | get_position, get_requisition, get_pre_hire, list_onboarding_tasks, get_cost_center, list_provisioning_tickets, update_requisition_target_date, create_provisioning_request | read × 6, T1 × 1, T2 × 1 |
| workday-soap | preview_hire_employee, hire_employee, preview_costing_allocation, assign_costing_allocation | read × 2, T3 × 2 |
| workday-data | list_pending_hires, get_readiness_row | read × 2 |
| policy-docs | list_policies, lookup_policy | read × 2 |

## Idempotency & Retry

All writes use stable Idempotency-Key headers (SHA256 hash of canonical args). If the same args are sent twice:
- First attempt: executes and returns idempotency_key in response
- Retry: server recognizes key, returns cached result without side effects

## PII Handling

All REST responses have PII fields stripped recursively:
- nationalId, nationalID, ssn
- bankAccount, iban
- dateOfBirth
- homeAddress
- passportNumber

SOAP responses are returned raw (assume already sanitized by backend).

## Development Notes

- Mock tenant is FastAPI + DuckDB (in-memory, ephemeral)
- Seed data loads from `mock_tenant/data/seed.yaml` at startup
- Faults are injected at seed time; edit seed.yaml to add/remove blockers
- MCP servers are stateless (no secrets stored; all auth via env vars)
- Gate hook is called by Claude Code before every tool dispatch
- Audit log is append-only (never truncated; hash-chain possible for forensics)

## GitHub Deployment

Push to a GitHub repo with this structure:

```
dayone-agent/
├── .mcp.json
├── .claude/
│   ├── hooks/
│   │   └── gate.py
│   └── audit.log
├── CLAUDE.md
├── pyproject.toml
├── policy/
│   └── tiers.yaml
├── mock_tenant/
│   ├── app.py
│   └── data/
│       └── seed.yaml
└── mcp_servers/
    ├── common.py
    ├── workday_hcm.py
    ├── workday_soap.py
    ├── workday_data.py
    └── policy_docs.py
```

Deploy via Claude Code:

```bash
git clone https://github.com/<user>/dayone-agent.git
cd dayone-agent
python -m pip install -e .
claude code
```

---

**Built for Forward Deployment Engineer interview.**
Demonstrates: Workday API expertise, MCP integration, approval gating, audit logging, and interactive agent design.
