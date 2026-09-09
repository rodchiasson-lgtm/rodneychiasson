"""Mock Workday tenant for the DayOne Readiness Agent.

Endpoint parity with the surfaces the MCP servers call:

  REST   /ccx/api/v1/{tenant}/staffing/v1/positions/{id}
         /ccx/api/v1/{tenant}/staffing/v1/preHires[/{id}[/onboardingTasks]]
         /ccx/api/v1/{tenant}/recruiting/v1/jobRequisitions/{id}   (GET, PATCH)
         /ccx/api/v1/{tenant}/financialManagement/v1/costCenters/{id}
  SOAP   /ccx/service/{tenant}/Staffing/v43.0                       Hire_Employee
         /ccx/service/{tenant}/Financial_Management/v43.0           Assign_Costing_Allocation
  PRISM  /ccx/api/prismAnalytics/v3/{tenant}/datasets/hire_readiness
  ITSM   /itsm/tickets                                              (GET, POST)
  ADMIN  /__admin/reset · /__admin/refresh · /__admin/fault · /__admin/state

REST requires `Authorization: Bearer <token>`; SOAP requires a WS-Security Username.
Writes are idempotent on the `Idempotency-Key` header (REST) or <wd:Idempotency_Key>
element (SOAP): a repeated key returns the original result without re-applying.
"""
from __future__ import annotations

import copy
import datetime as dt
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import duckdb
import yaml
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request, Response

HERE = Path(__file__).parent
SEED = HERE / "data" / "seed.yaml"
REST_TOKEN = os.environ.get("MOCK_TENANT_TOKEN", "mock-token")
ISU_USER = os.environ.get("MOCK_TENANT_ISU", "ISU_DayOne@acme_corp")

app = FastAPI(title="Mock Workday tenant", version="0.1")


# --------------------------------------------------------------------------- state
def _today() -> dt.date:
    return dt.date.today()


def _resolve(d: str | None) -> str | None:
    """'+3d' -> ISO date three days from today; ISO dates pass through."""
    if d is None:
        return None
    if isinstance(d, str) and d.startswith("+") and d.endswith("d"):
        return (_today() + dt.timedelta(days=int(d[1:-1]))).isoformat()
    return str(d)


def load_seed() -> dict[str, Any]:
    raw = yaml.safe_load(SEED.read_text())
    s: dict[str, Any] = {"tenant": raw["tenant"], "idempotency": {}, "faults": {}, "events": []}
    s["cost_centers"] = {c["id"]: c for c in raw["cost_centers"]}
    s["orgs"] = {o["id"]: o for o in raw["supervisory_orgs"]}
    s["workers"] = {w["id"]: w for w in raw["workers"]}
    s["positions"] = {}
    for p in raw["positions"]:
        p = dict(p)
        p["available_from"] = _resolve(p["available_from"])
        s["positions"][p["id"]] = p
    s["requisitions"] = {}
    for r in raw["requisitions"]:
        r = dict(r)
        r["target_hire_date"] = _resolve(r["target_hire_date"])
        s["requisitions"][r["id"]] = r
    s["pre_hires"] = {}
    for ph in raw["pre_hires"]:
        ph = copy.deepcopy(ph)
        ph["start_date"] = _resolve(ph["start_date"])
        if ph.get("costing"):
            ph["costing"]["effective"] = _resolve(ph["costing"]["effective"])
        ph["worker_id"] = None
        fault = ph.pop("fault", None)
        if fault:
            s["faults"][ph["id"]] = fault
        s["pre_hires"][ph["id"]] = ph
    s["itsm"] = {t["id"]: dict(t) for t in raw["itsm_tickets"]}
    s["onboarding_tasks"] = [dict(t) for t in raw["onboarding_tasks"]]
    s["next_ticket"] = 600
    s["next_worker"] = 1100
    return s


STATE: dict[str, Any] = load_seed()
PRISM: dict[str, Any] = {"rows": [], "refreshed_at": None}


# ---------------------------------------------------------------------- auth deps
def rest_auth(authorization: str | None = Header(default=None)) -> None:
    if authorization != f"Bearer {REST_TOKEN}":
        raise HTTPException(401, {"error": "invalid or missing bearer token"})


def tenant_check(tenant: str) -> None:
    if tenant != STATE["tenant"]:
        raise HTTPException(404, {"error": f"unknown tenant {tenant}"})


# ------------------------------------------------------------------ readiness calc
def stage_states(ph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Compute the five stage states for one pre-hire. Pure function of STATE."""
    pos = STATE["positions"][ph["position"]]
    req = STATE["requisitions"][ph["requisition"]]
    tickets = [t for t in STATE["itsm"].values() if t["pre_hire"] == ph["id"]]
    out: dict[str, dict[str, Any]] = {}

    if pos.get("hiring_freeze"):
        out["position"] = {"state": "blocked", "detail": f"Position {pos['id']} under hiring freeze: {pos.get('freeze_reason')}"}
    else:
        out["position"] = {"state": "ok", "detail": f"{pos['id']} {pos['job_profile']} available from {pos['available_from']}"}

    if req["status"] != "Filled":
        out["requisition"] = {"state": "blocked", "detail": f"Requisition {req['id']} is {req['status']}"}
    elif req["target_hire_date"] != ph["start_date"]:
        out["requisition"] = {"state": "warn", "detail": f"Target hire date {req['target_hire_date']} differs from confirmed start {ph['start_date']}"}
    else:
        out["requisition"] = {"state": "ok", "detail": f"{req['id']} filled, target {req['target_hire_date']}"}

    he = ph["hire_event"]
    if he["status"] == "Successfully Completed":
        out["hire"] = {"state": "ok", "detail": f"Hire event complete; worker {ph.get('worker_id') or 'pending effective date'}"}
    elif he["status"] == "In Progress":
        out["hire"] = {"state": "pending", "detail": f"Hire business process awaiting: {he['awaiting_step']}"}
    else:
        out["hire"] = {"state": "not_started", "detail": "Hire event not started"}

    kinds = {t["type"]: t["status"] for t in tickets}
    missing = [k for k in ("laptop", "identity") if k not in kinds]
    if missing:
        out["provisioning"] = {"state": "blocked", "detail": f"No provisioning ticket for: {', '.join(missing)}"}
    elif any(v not in ("Fulfilled", "In Progress") for v in kinds.values()):
        out["provisioning"] = {"state": "warn", "detail": f"Ticket states: {kinds}"}
    else:
        out["provisioning"] = {"state": "ok", "detail": f"Tickets: {kinds}"}

    c = ph.get("costing")
    if not c:
        out["costing"] = {"state": "not_started", "detail": "No costing allocation assigned"}
    else:
        bad = [s["cost_center"] for s in c["split"] if STATE["cost_centers"].get(s["cost_center"], {}).get("status") != "Active"]
        if bad:
            cc = STATE["cost_centers"][bad[0]]
            out["costing"] = {"state": "blocked", "detail": f"Allocation points at {bad[0]} ({cc['name']}) which is {cc['status']}; successor {cc.get('successor')}"}
        else:
            alloc = ", ".join(f"{s['cost_center']} {s['pct']}%" for s in c["split"])
            out["costing"] = {"state": "ok", "detail": f"Allocated to {alloc} effective {c['effective']}"}
    return out


def build_prism() -> None:
    """Rebuild the hire_readiness dataset (a DuckDB table) from transactional state."""
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE hire_readiness (
        pre_hire_id VARCHAR, name VARCHAR, position_id VARCHAR, requisition_id VARCHAR,
        supervisory_org VARCHAR, start_date DATE, days_to_start INTEGER,
        position_state VARCHAR, requisition_state VARCHAR, hire_state VARCHAR,
        provisioning_state VARCHAR, costing_state VARCHAR, blocker_count INTEGER)""")
    today = _today()
    for ph in STATE["pre_hires"].values():
        st = stage_states(ph)
        blockers = sum(1 for v in st.values() if v["state"] in ("blocked", "not_started", "pending"))
        con.execute("INSERT INTO hire_readiness VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            ph["id"], ph["name"], ph["position"], ph["requisition"], STATE["positions"][ph["position"]]["org"],
            ph["start_date"], (dt.date.fromisoformat(ph["start_date"]) - today).days,
            st["position"]["state"], st["requisition"]["state"], st["hire"]["state"],
            st["provisioning"]["state"], st["costing"]["state"], blockers])
    cols = [d[0] for d in con.execute("SELECT * FROM hire_readiness ORDER BY start_date").description]
    rows = con.execute("SELECT * FROM hire_readiness ORDER BY start_date").fetchall()
    PRISM["rows"] = [dict(zip(cols, [v.isoformat() if isinstance(v, dt.date) else v for v in r])) for r in rows]
    PRISM["refreshed_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


build_prism()


# ----------------------------------------------------------------------- REST HCM
@app.get("/ccx/api/v1/{tenant}/staffing/v1/positions/{pid}", dependencies=[Depends(rest_auth)])
def get_position(tenant: str, pid: str):
    tenant_check(tenant)
    p = STATE["positions"].get(pid)
    if not p:
        raise HTTPException(404, {"error": f"position {pid} not found"})
    org = STATE["orgs"][p["org"]]
    return {**p, "supervisoryOrganization": {"id": org["id"], "descriptor": org["name"], "manager": STATE["workers"][org["manager"]]["name"]},
            "defaultCostCenter": org["default_cost_center"]}


@app.get("/ccx/api/v1/{tenant}/staffing/v1/preHires", dependencies=[Depends(rest_auth)])
def list_pre_hires(tenant: str):
    tenant_check(tenant)
    return {"total": len(STATE["pre_hires"]), "data": [{"id": p["id"], "descriptor": p["name"], "startDate": p["start_date"]} for p in STATE["pre_hires"].values()]}


@app.get("/ccx/api/v1/{tenant}/staffing/v1/preHires/{phid}", dependencies=[Depends(rest_auth)])
def get_pre_hire(tenant: str, phid: str):
    tenant_check(tenant)
    ph = STATE["pre_hires"].get(phid)
    if not ph:
        raise HTTPException(404, {"error": f"pre-hire {phid} not found"})
    return {"id": ph["id"], "descriptor": ph["name"], "candidate": ph["candidate"], "position": ph["position"],
            "jobRequisition": ph["requisition"], "startDate": ph["start_date"], "hireEvent": ph["hire_event"],
            "workerId": ph.get("worker_id"), "costingAllocation": ph.get("costing"),
            # PII the MCP layer must strip before the model sees it:
            "nationalId": "***-**-" + phid[-4:], "bankAccount": {"iban": "GB00MOCK" + phid[-4:] + "0000"}}


@app.get("/ccx/api/v1/{tenant}/staffing/v1/preHires/{phid}/onboardingTasks", dependencies=[Depends(rest_auth)])
def onboarding_tasks(tenant: str, phid: str):
    tenant_check(tenant)
    if phid not in STATE["pre_hires"]:
        raise HTTPException(404, {"error": f"pre-hire {phid} not found"})
    return {"data": [t for t in STATE["onboarding_tasks"] if t["pre_hire"] == phid]}


@app.get("/ccx/api/v1/{tenant}/recruiting/v1/jobRequisitions/{rid}", dependencies=[Depends(rest_auth)])
def get_requisition(tenant: str, rid: str):
    tenant_check(tenant)
    r = STATE["requisitions"].get(rid)
    if not r:
        raise HTTPException(404, {"error": f"requisition {rid} not found"})
    return {"id": r["id"], "position": r["position"], "status": r["status"], "targetHireDate": r["target_hire_date"], "candidate": r["candidate"]}


@app.patch("/ccx/api/v1/{tenant}/recruiting/v1/jobRequisitions/{rid}", dependencies=[Depends(rest_auth)])
def patch_requisition(tenant: str, rid: str, body: dict = Body(...), idempotency_key: str | None = Header(default=None)):
    tenant_check(tenant)
    r = STATE["requisitions"].get(rid)
    if not r:
        raise HTTPException(404, {"error": f"requisition {rid} not found"})
    if idempotency_key and idempotency_key in STATE["idempotency"]:
        return STATE["idempotency"][idempotency_key]
    before = r["target_hire_date"]
    r["target_hire_date"] = body["targetHireDate"]
    result = {"id": rid, "targetHireDate": r["target_hire_date"], "previousTargetHireDate": before, "applied": True}
    if idempotency_key:
        STATE["idempotency"][idempotency_key] = result
    STATE["events"].append({"kind": "requisition.patch", "id": rid, "before": before, "after": r["target_hire_date"]})
    return result


@app.get("/ccx/api/v1/{tenant}/financialManagement/v1/costCenters/{ccid}", dependencies=[Depends(rest_auth)])
def get_cost_center(tenant: str, ccid: str):
    tenant_check(tenant)
    c = STATE["cost_centers"].get(ccid)
    if not c:
        raise HTTPException(404, {"error": f"cost center {ccid} not found"})
    return c


# ---------------------------------------------------------------------------- SOAP
WD = "urn:com.workday/bsvc"
NS = {"wd": WD, "soapenv": "http://schemas.xmlsoap.org/soap/envelope/", "wsse": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"}


def _soap_fault(code: str, msg: str, status: int = 500) -> Response:
    body = f"""<soapenv:Envelope xmlns:soapenv="{NS['soapenv']}"><soapenv:Body><soapenv:Fault>
<faultcode>{code}</faultcode><faultstring>{msg}</faultstring></soapenv:Fault></soapenv:Body></soapenv:Envelope>"""
    return Response(content=body, media_type="text/xml", status_code=status)


def _soap_ok(inner: str) -> Response:
    body = f"""<soapenv:Envelope xmlns:soapenv="{NS['soapenv']}" xmlns:wd="{WD}"><soapenv:Body>{inner}</soapenv:Body></soapenv:Envelope>"""
    return Response(content=body, media_type="text/xml")


def _parse_soap(raw: bytes) -> tuple[ET.Element, ET.Element]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise HTTPException(400, {"error": f"malformed SOAP: {e}"})
    user = root.find(".//wsse:Username", NS)
    if user is None or user.text != ISU_USER:
        raise HTTPException(401, {"error": "WS-Security Username missing or not an authorised ISU"})
    body = root.find("soapenv:Body", NS)
    if body is None or len(body) == 0:
        raise HTTPException(400, {"error": "empty SOAP body"})
    return root, body[0]


def _txt(el: ET.Element, path: str) -> str | None:
    f = el.find(path, NS)
    return f.text if f is not None else None


def _consume_fault(phid: str, fault: str) -> bool:
    """Return True (and clear) if the pre-hire has the given one-shot fault armed."""
    if STATE["faults"].get(phid) == fault:
        STATE["faults"].pop(phid)
        return True
    return False


@app.post("/ccx/service/{tenant}/Staffing/v43.0")
async def soap_staffing(tenant: str, request: Request):
    tenant_check(tenant)
    _, op = _parse_soap(await request.body())
    if op.tag != f"{{{WD}}}Hire_Employee_Request":
        return _soap_fault("soapenv:Client", f"unsupported operation {op.tag}", 400)
    validate_only = (_txt(op, "wd:Business_Process_Parameters/wd:Validate_Only") or "false").lower() == "true"
    phid = _txt(op, "wd:Hire_Employee_Data/wd:Applicant_Reference/wd:ID")
    pos = _txt(op, "wd:Hire_Employee_Data/wd:Position_Reference/wd:ID")
    hire_date = _txt(op, "wd:Hire_Employee_Data/wd:Hire_Date")
    key = _txt(op, "wd:Business_Process_Parameters/wd:Idempotency_Key")
    ph = STATE["pre_hires"].get(phid)
    errors = []
    if not ph:
        errors.append(f"Pre-hire {phid} not found")
    else:
        p = STATE["positions"].get(pos)
        if not p:
            errors.append(f"Position {pos} not found")
        elif p.get("hiring_freeze"):
            errors.append(f"Position {pos} is under hiring freeze: {p.get('freeze_reason')}")
        if ph["hire_event"]["status"] == "Successfully Completed":
            errors.append(f"Pre-hire {phid} already hired")
        if ph["hire_event"]["status"] == "In Progress":
            errors.append(f"Hire business process already in progress, awaiting {ph['hire_event']['awaiting_step']}")
        if hire_date != ph["start_date"]:
            errors.append(f"Hire_Date {hire_date} does not match confirmed start date {ph['start_date']}")
    diff = f"""<wd:Proposed_Change><wd:Field>hireEvent.status</wd:Field><wd:Before>{ph['hire_event']['status'] if ph else ''}</wd:Before><wd:After>Successfully Completed</wd:After></wd:Proposed_Change>
<wd:Proposed_Change><wd:Field>workerId</wd:Field><wd:Before/><wd:After>W-{STATE['next_worker']}</wd:After></wd:Proposed_Change>"""
    if errors:
        vs = "".join(f"<wd:Validation_Error><wd:Message>{e}</wd:Message></wd:Validation_Error>" for e in errors)
        return _soap_ok(f"<wd:Hire_Employee_Response><wd:Validate_Only>{str(validate_only).lower()}</wd:Validate_Only><wd:Validation_Errors>{vs}</wd:Validation_Errors>{diff}</wd:Hire_Employee_Response>")
    if validate_only:
        return _soap_ok(f"<wd:Hire_Employee_Response><wd:Validate_Only>true</wd:Validate_Only><wd:Valid>true</wd:Valid>{diff}</wd:Hire_Employee_Response>")
    if key and key in STATE["idempotency"]:
        return _soap_ok(STATE["idempotency"][key])
    if _consume_fault(phid, "soap_timeout"):
        return _soap_fault("soapenv:Server", "Gateway timeout (simulated)", 504)
    wid = f"W-{STATE['next_worker']}"
    STATE["next_worker"] += 1
    ph["hire_event"] = {"status": "Successfully Completed", "awaiting_step": None}
    ph["worker_id"] = wid
    resp = f"<wd:Hire_Employee_Response><wd:Employee_Reference><wd:ID wd:type=\"Employee_ID\">{wid}</wd:ID></wd:Employee_Reference><wd:Applied>true</wd:Applied></wd:Hire_Employee_Response>"
    if key:
        STATE["idempotency"][key] = resp
    STATE["events"].append({"kind": "hire", "pre_hire": phid, "worker": wid})
    return _soap_ok(resp)


@app.post("/ccx/service/{tenant}/Financial_Management/v43.0")
async def soap_fin(tenant: str, request: Request):
    tenant_check(tenant)
    _, op = _parse_soap(await request.body())
    if op.tag != f"{{{WD}}}Assign_Costing_Allocation_Request":
        return _soap_fault("soapenv:Client", f"unsupported operation {op.tag}", 400)
    validate_only = (_txt(op, "wd:Business_Process_Parameters/wd:Validate_Only") or "false").lower() == "true"
    key = _txt(op, "wd:Business_Process_Parameters/wd:Idempotency_Key")
    phid = _txt(op, "wd:Costing_Allocation_Data/wd:Pre_Hire_Reference/wd:ID")
    effective = _txt(op, "wd:Costing_Allocation_Data/wd:Effective_Date")
    split = [(_txt(d, "wd:Cost_Center_Reference/wd:ID"), float(_txt(d, "wd:Distribution_Percent") or 0))
             for d in op.findall("wd:Costing_Allocation_Data/wd:Costing_Allocation_Detail", NS)]
    ph = STATE["pre_hires"].get(phid)
    errors = []
    if not ph:
        errors.append(f"Pre-hire {phid} not found")
    if not split:
        errors.append("No Costing_Allocation_Detail provided")
    for cc, pct in split:
        c = STATE["cost_centers"].get(cc)
        if not c:
            errors.append(f"Cost center {cc} not found")
        elif c["status"] != "Active":
            errors.append(f"Cost center {cc} is {c['status']}" + (f"; successor is {c['successor']}" if c.get("successor") else ""))
    if split and abs(sum(p for _, p in split) - 100.0) > 0.01:
        errors.append(f"Distribution percentages sum to {sum(p for _, p in split)}, not 100")
    if ph and effective and effective < ph["start_date"]:
        errors.append(f"Effective date {effective} precedes start date {ph['start_date']}")
    before = ph.get("costing") if ph else None
    before_s = ", ".join(f"{s['cost_center']} {s['pct']}%" for s in before["split"]) if before else "(none)"
    after_s = ", ".join(f"{cc} {pct:g}%" for cc, pct in split)
    diff = f"<wd:Proposed_Change><wd:Field>costingAllocation</wd:Field><wd:Before>{before_s}</wd:Before><wd:After>{after_s} effective {effective}</wd:After></wd:Proposed_Change>"
    if errors:
        vs = "".join(f"<wd:Validation_Error><wd:Message>{e}</wd:Message></wd:Validation_Error>" for e in errors)
        return _soap_ok(f"<wd:Assign_Costing_Allocation_Response><wd:Validate_Only>{str(validate_only).lower()}</wd:Validate_Only><wd:Validation_Errors>{vs}</wd:Validation_Errors>{diff}</wd:Assign_Costing_Allocation_Response>")
    if validate_only:
        return _soap_ok(f"<wd:Assign_Costing_Allocation_Response><wd:Validate_Only>true</wd:Validate_Only><wd:Valid>true</wd:Valid>{diff}</wd:Assign_Costing_Allocation_Response>")
    if key and key in STATE["idempotency"]:
        return _soap_ok(STATE["idempotency"][key])
    if _consume_fault(phid, "soap_timeout"):
        return _soap_fault("soapenv:Server", "Gateway timeout (simulated)", 504)
    ph["costing"] = {"cost_center": split[0][0], "split": [{"cost_center": cc, "pct": pct} for cc, pct in split], "effective": effective}
    resp = f"<wd:Assign_Costing_Allocation_Response><wd:Applied>true</wd:Applied>{diff}</wd:Assign_Costing_Allocation_Response>"
    if key:
        STATE["idempotency"][key] = resp
    STATE["events"].append({"kind": "costing", "pre_hire": phid, "after": after_s})
    return _soap_ok(resp)


# --------------------------------------------------------------------------- PRISM
@app.get("/ccx/api/prismAnalytics/v3/{tenant}/datasets/hire_readiness", dependencies=[Depends(rest_auth)])
def prism_dataset(tenant: str, days: int = 14):
    tenant_check(tenant)
    rows = [r for r in PRISM["rows"] if 0 <= r["days_to_start"] <= days]
    return {"dataset": "hire_readiness", "refreshedAt": PRISM["refreshed_at"], "windowDays": days, "total": len(rows), "data": rows}


@app.get("/prism/readiness/{phid}", dependencies=[Depends(rest_auth)])
def prism_readiness_detail(phid: str):
    """Deep-dive readiness snapshot for a single pre-hire (all 5 stages)."""
    ph = STATE["pre_hires"].get(phid)
    if not ph:
        raise HTTPException(404, {"error": f"pre-hire {phid} not found"})

    return {
        "pre_hire_id": phid,
        "name": ph["name"],
        "start_date": ph["start_date"],
        "stages": stage_states(ph),
        "faults": [STATE["faults"].get(phid)] if phid in STATE["faults"] else [],
    }


# ---------------------------------------------------------------------------- ITSM
@app.get("/itsm/tickets", dependencies=[Depends(rest_auth)])
def list_tickets(pre_hire: str | None = None):
    ts = [t for t in STATE["itsm"].values() if not pre_hire or t["pre_hire"] == pre_hire]
    return {"data": ts}


@app.post("/itsm/tickets", dependencies=[Depends(rest_auth)])
def create_ticket(body: dict = Body(...), idempotency_key: str | None = Header(default=None)):
    if idempotency_key and idempotency_key in STATE["idempotency"]:
        return STATE["idempotency"][idempotency_key]
    created = []
    for kind in body.get("types", []):
        tid = f"IT-{STATE['next_ticket']}"
        STATE["next_ticket"] += 1
        STATE["itsm"][tid] = {"id": tid, "pre_hire": body["pre_hire"], "type": kind, "status": "Open", "due": body.get("due")}
        created.append(STATE["itsm"][tid])
    result = {"created": created}
    if idempotency_key:
        STATE["idempotency"][idempotency_key] = result
    STATE["events"].append({"kind": "itsm.create", "pre_hire": body["pre_hire"], "tickets": [t["id"] for t in created]})
    return result


# --------------------------------------------------------------------------- ADMIN
@app.post("/__admin/reset")
def admin_reset():
    global STATE
    STATE = load_seed()
    build_prism()
    return {"reset": True, "pre_hires": len(STATE["pre_hires"])}


@app.post("/__admin/refresh")
def admin_refresh():
    build_prism()
    return {"refreshedAt": PRISM["refreshed_at"]}


@app.post("/__admin/fault")
def admin_fault(body: dict = Body(...)):
    """Arm a one-shot fault: {"pre_hire": "PH-9005", "fault": "soap_timeout"}"""
    STATE["faults"][body["pre_hire"]] = body["fault"]
    return {"faults": STATE["faults"]}


@app.get("/__admin/state")
def admin_state():
    return {"pre_hires": {k: stage_states(v) for k, v in STATE["pre_hires"].items()}, "faults": STATE["faults"], "events": STATE["events"]}


@app.get("/")
def root():
    return {"service": "mock workday tenant", "tenant": STATE["tenant"], "prismRefreshedAt": PRISM["refreshed_at"]}
