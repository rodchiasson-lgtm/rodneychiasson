"""Shared plumbing for the DayOne MCP servers.

- backend selection (WORKDAY_BACKEND=mock|tenant), base URLs and credentials
- REST and SOAP clients with PII stripping
- idempotency keys for proposed writes
- tier lookup so every tool description advertises its risk tier
"""
from __future__ import annotations

import hashlib
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
TIERS = yaml.safe_load((ROOT / "policy" / "tiers.yaml").read_text())

BACKEND = os.environ.get("WORKDAY_BACKEND", "mock")
TENANT = os.environ.get("WORKDAY_TENANT", "acme_corp")
BASE = os.environ.get("WORKDAY_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
REST_TOKEN = os.environ.get("WORKDAY_REST_TOKEN", "mock-token")
ISU_USER = os.environ.get("WORKDAY_ISU_USER", "ISU_DayOne@acme_corp")
ISU_PASSWORD = os.environ.get("WORKDAY_ISU_PASSWORD", "mock-password")
SOAP_VERSION = os.environ.get("WORKDAY_SOAP_VERSION", "v43.0")

# Fields the model must never see. Stripped recursively from every REST response.
PII_FIELDS = {"nationalId", "nationalID", "ssn", "bankAccount", "iban", "dateOfBirth", "homeAddress", "passportNumber"}


def tier_of(server: str, tool: str) -> str:
    return TIERS["tools"].get(server, {}).get(tool, "T3")  # unknown tools are treated as T3


def describe(server: str, tool: str, text: str) -> str:
    """Tool description with the tier stamped on the front so it is visible in the tool list."""
    return f"[{tier_of(server, tool)}] {text}"


def strip_pii(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: strip_pii(v) for k, v in obj.items() if k not in PII_FIELDS}
    if isinstance(obj, list):
        return [strip_pii(v) for v in obj]
    return obj


def idem_key(*parts: Any) -> str:
    """Stable idempotency key for a proposed write: sha256 of the canonical arguments."""
    raw = json.dumps(parts, sort_keys=True, default=str)
    return "dk_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


# ------------------------------------------------------------------------- REST
def rest_get(path: str, **params: Any) -> dict[str, Any]:
    url = f"{BASE}{path}"
    with httpx.Client(timeout=15) as c:
        r = c.get(url, params=params or None, headers={"Authorization": f"Bearer {REST_TOKEN}"})
    if r.status_code >= 400:
        return {"error": f"HTTP {r.status_code}", "detail": _safe_json(r), "url": url}
    return strip_pii(r.json())


def rest_write(method: str, path: str, body: dict[str, Any], key: str) -> dict[str, Any]:
    url = f"{BASE}{path}"
    with httpx.Client(timeout=15) as c:
        r = c.request(method, url, json=body, headers={"Authorization": f"Bearer {REST_TOKEN}", "Idempotency-Key": key})
    if r.status_code >= 400:
        return {"error": f"HTTP {r.status_code}", "detail": _safe_json(r), "url": url, "idempotency_key": key}
    return {**strip_pii(r.json()), "idempotency_key": key}


def _safe_json(r: httpx.Response) -> Any:
    try:
        return r.json()
    except Exception:
        return r.text[:500]


# ------------------------------------------------------------------------- SOAP
WD = "urn:com.workday/bsvc"
NS = {"wd": WD, "soapenv": "http://schemas.xmlsoap.org/soap/envelope/"}


def soap_envelope(operation_xml: str) -> str:
    return f"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:wd="{WD}"
  xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
  <soapenv:Header><wsse:Security><wsse:UsernameToken>
    <wsse:Username>{ISU_USER}</wsse:Username><wsse:Password>{ISU_PASSWORD}</wsse:Password>
  </wsse:UsernameToken></wsse:Security></soapenv:Header>
  <soapenv:Body>{operation_xml}</soapenv:Body>
</soapenv:Envelope>"""


def soap_call(service: str, operation_xml: str) -> dict[str, Any]:
    """POST a SOAP operation and normalise the response into a dict the model can read."""
    url = f"{BASE}/ccx/service/{TENANT}/{service}/{SOAP_VERSION}"
    try:
        with httpx.Client(timeout=20) as c:
            r = c.post(url, content=soap_envelope(operation_xml), headers={"Content-Type": "text/xml"})
    except httpx.TimeoutException:
        return {"error": "timeout", "retryable": True, "service": service}
    if r.status_code >= 400 and "soapenv:Fault" not in r.text:
        return {"error": f"HTTP {r.status_code}", "detail": r.text[:400], "retryable": r.status_code in (502, 503, 504)}
    root = ET.fromstring(r.text)
    fault = root.find(".//soapenv:Fault", NS)
    if fault is not None:
        code = fault.findtext("faultcode") or ""
        return {"error": "soap_fault", "code": code, "message": fault.findtext("faultstring"), "retryable": "Server" in code}
    body = root.find("soapenv:Body", NS)
    resp = body[0]
    out: dict[str, Any] = {"operation": resp.tag.split("}")[-1]}
    valid = resp.findtext("wd:Valid", None, NS)
    if valid is not None:
        out["valid"] = valid == "true"
    errs = [e.findtext("wd:Message", "", NS) for e in resp.findall("wd:Validation_Errors/wd:Validation_Error", NS)]
    if errs:
        out["valid"] = False
        out["validation_errors"] = errs
    applied = resp.findtext("wd:Applied", None, NS)
    if applied is not None:
        out["applied"] = applied == "true"
    emp = resp.findtext("wd:Employee_Reference/wd:ID", None, NS)
    if emp:
        out["worker_id"] = emp
    changes = [{"field": c.findtext("wd:Field", "", NS), "before": c.findtext("wd:Before", "", NS), "after": c.findtext("wd:After", "", NS)}
               for c in resp.findall("wd:Proposed_Change", NS)]
    if changes:
        out["diff"] = changes
    return out
