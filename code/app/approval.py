"""Approval step and writes to the mock helpdesk.

A run that needs the support lead (every refund and every handover) is saved in code/runs/<run_id>.json
and nothing is written. `python -m app approve <run_id> --action ...` resumes it:
  approve -> ticket (and refund requests) written
  reject  -> nothing written; the run is closed as rejected
  change  -> the lead adjusts the refund amount and/or adds a note, then it is written
Writes are idempotent per run_id, so a resumed or repeated approval never writes twice.
"""
from __future__ import annotations

import datetime as dt
import json
import re

from . import config
from .trace import Trace


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _state_path(run_id: str):
    if not re.fullmatch(r"RUN-[\w\-]+", run_id or ""):
        raise SystemExit(f"Not a valid run ID: {run_id!r}")
    return config.RUNS_DIR / f"{run_id}.json"


def save_pending(result: dict, ctx: dict) -> None:
    config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    state = {"run_id": result["run_id"], "status": "awaiting_approval", "created_at": _now(),
             "result": result, "context": ctx}
    _state_path(result["run_id"]).write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _already_written(path, run_id: str) -> bool:
    if not path.is_file():
        return False
    with path.open(encoding="utf-8") as f:
        return any(json.loads(line).get("run_id") == run_id for line in f if line.strip())


def _append(path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def commit(result: dict, approved_by: str | None, trace: Trace | None = None, note: str | None = None) -> str:
    """Write the ticket, and each approved refund line, to the mock system. Returns the ticket ID."""
    tickets = config.MOCK_DIR / "helpdesk_tickets.jsonl"
    refunds = config.MOCK_DIR / "refund_requests.jsonl"
    ticket_id = "TKT-" + result["run_id"].removeprefix("RUN-")
    if _already_written(tickets, result["run_id"]):
        return ticket_id
    status = {"handover": "assigned_to_support_lead", "reply_with_refund": "replied_refund_approved"}.get(
        result["decision"], "replied")
    _append(tickets, {
        "ticket_id": ticket_id, "run_id": result["run_id"], "trace_id": result["trace_id"],
        "case_id": result["case_id"], "email_id": result["email_id"], "created_at": _now(),
        "status": status, "decision": result["decision"], "reason_code": result["reason_code"],
        "priority": result["priority"], "tags": result["tags"], "amount": result["amount"],
        "citations": result["citations"],
        "requests": [{k: r.get(k) for k in ("id", "type", "order_id", "outcome", "reason_code")} for r in result["requests"]],
        "draft_reply": result["draft_reply"], "approved_by": approved_by, "approval_note": note,
        "handover_note": result.get("handover_note"),
    })
    if result.get("refund") and result["decision"] == "reply_with_refund":
        for i, line in enumerate(result["refund"]["lines"], 1):
            _append(refunds, {"refund_request_id": f"RR-{result['run_id'].removeprefix('RUN-')}-{i}",
                              "run_id": result["run_id"], "ticket_id": ticket_id, "case_id": result["case_id"],
                              "email_id": result["email_id"], "order_id": line["order_id"], "sku": line["sku"],
                              "item": line["name"], "qty": line["qty"], "unit_price": line["unit_price"],
                              "amount": line["amount"], "currency": "INR", "policy": line["rule"],
                              "approved_by": approved_by, "approved_at": _now(), "status": "approved"})
    if trace:
        trace.log("writer", "mock_system_written", ticket_id=ticket_id, approved_by=approved_by,
                  refunds=len(result["refund"]["lines"]) if result.get("refund") else 0)
    return ticket_id


def approve(run_id: str, action: str, amount: float | None = None, note: str | None = None) -> dict:
    path = _state_path(run_id)
    if not path.is_file():
        raise SystemExit(f"No paused run {run_id} in {config.RUNS_DIR}")
    state = json.loads(path.read_text(encoding="utf-8"))
    if state["status"] != "awaiting_approval":
        raise SystemExit(f"Run {run_id} is already {state['status']}; nothing to do.")
    result = state["result"]
    trace = Trace(trace_id=result["trace_id"], run_id=run_id)
    trace.log("approval", "decision_received", action=action, amount=amount, note=note)

    if action == "reject":
        state["status"] = result["status"] = "rejected"
        if result.get("refund"):
            result["refund"]["status"] = "rejected"
        trace.log("approval", "rejected", note="nothing written to the mock system")
    else:
        if action == "change":
            if amount is None and not note:
                raise SystemExit("--action change needs --amount and/or --note")
            if amount is not None:
                if not result.get("refund"):
                    raise SystemExit("This run has no refund to change; use --note only.")
                _change_amount(result, state.get("context", {}), float(amount))
        if result.get("refund"):
            result["refund"]["status"] = "approved"
        state["status"] = result["status"] = "approved_with_changes" if action == "change" else "approved"
        result["ticket_id"] = commit(result, "support_lead", trace, note)
    state["decided_at"] = _now()
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def _change_amount(result: dict, ctx: dict, new_total: float) -> None:
    """The lead sets a different refund total: lines are scaled to it and the reply is updated to match."""
    from .agents.policy import rupees
    old = result["refund"]["total"]
    if new_total < 0:
        raise SystemExit("--amount must be zero or more")
    factor = new_total / old if old else 0
    for line in result["refund"]["lines"]:
        line["amount"] = round(line["amount"] * factor, 2)
    drift = round(new_total - sum(l["amount"] for l in result["refund"]["lines"]), 2)
    if result["refund"]["lines"]:
        result["refund"]["lines"][0]["amount"] = round(result["refund"]["lines"][0]["amount"] + drift, 2)
    result["refund"]["total"] = result["amount"] = round(new_total, 2)
    result["draft_reply"] = result["draft_reply"].replace(rupees(old), rupees(new_total)) \
                                                 .replace(f"₹{old:,.0f}", rupees(new_total))
    result["tags"] = list(dict.fromkeys(result["tags"] + ["amount_changed_by_lead"]))
