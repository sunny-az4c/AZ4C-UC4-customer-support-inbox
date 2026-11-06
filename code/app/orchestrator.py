"""Orchestrator: runs one email through the agents and decides what runs next.

    read email -> identify sender -> triage
      escalation?  yes -> handover (EP 1/EP 2): nothing else is discussed
                   no  -> policy agent settles each request (knowledge agent for questions)
    -> aggregate one decision (RP 5) -> writer drafts the reply -> approval gate -> mock system writes
"""
from __future__ import annotations

from . import config
from .agents.policy import Resolver
from .agents.triage import triage
from .agents.writer import draft, handover_reply
from .approval import commit, save_pending
from .data import ORDER_ID_RE, DataError, Store
from .llm import LLM
from .tools import ToolError, Tools
from .trace import Trace, new_id

REASON_ORDER = ["LEGAL_OR_ESCALATION", "IDENTITY_MISMATCH", "REFUND_ELIGIBLE", "REFUND_NOT_ELIGIBLE",
                "NO_SOURCE_AVAILABLE", "RESOLVED"]                                            # RP 5.2


def _uniq(xs):
    return list(dict.fromkeys(x for x in xs if x))


def run_case(case: dict, auto_approve: bool) -> dict:
    case_id = case.get("case_id") or "UNKNOWN"
    email_id = (case.get("input") or {}).get("email_id", "")
    run_id = new_id("RUN")
    trace = Trace(run_id=run_id)
    trace.log("orchestrator", "run_started", case_id=case_id, email_id=email_id, auto_approve=auto_approve,
              data_dir=str(config.DATA_DIR))
    store = Store(config.DATA_DIR)
    tools, llm = Tools(store, trace), LLM(trace)

    base = {"case_id": case_id, "run_id": run_id, "trace_id": trace.trace_id, "email_id": email_id}
    try:
        email = tools.read_email(email_id)
        customer = tools.lookup_customer(email.sender)
    except ToolError as e:
        # Without the email we cannot do anything safely: a person takes it.
        result = {**base, "decision": "handover", "reason_code": "NO_SOURCE_AVAILABLE", "amount": None,
                  "citations": [], "needs_human": True, "requests": [], "refund": None, "draft_reply": "",
                  "tags": ["system_error"], "priority": "P3", "error": str(e)}
        return _finish(result, {}, trace, auto_approve)

    tri = triage(email, llm, trace)
    name = (customer["name"].split()[0] if customer else None) or tri.get("first_name") or "there"
    tags = []
    if tri["injection_detected"]:
        tags.append("instructions_in_email_ignored")
    if email.quoted:
        tags.append("thread")

    if tri["escalation"]:
        result, ctx = _handover(base, email, customer, tri, tools, name, trace)
    else:
        resolver = Resolver(email, customer, tools, llm, trace, len(tri["requests"]))
        resolutions = []
        for i, req in enumerate(tri["requests"], 1):
            try:
                resolutions.append(resolver.resolve(i, req))
            except ToolError as e:
                trace.log("orchestrator", "request_failed", request=i, error=str(e))
                resolutions.append({"id": f"R{i}", "type": req["type"], "summary": req["summary"],
                                    "order_id": req.get("order_id"), "items": req.get("items", []),
                                    "outcome": "tool_error", "reason_code": "NO_SOURCE_AVAILABLE", "citations": [],
                                    "amount": None, "refund_lines": [], "facts": {"error": str(e)}, "tags": ["tool_error"],
                                    "priority": "P3",
                                    "points": ["A member of our team is looking into this and will reply within one working day."]})
        result, ctx = _aggregate(base, resolutions, tri, tags, name, llm, trace)
    result["tags"] = _uniq(result["tags"] + tags + [f"lang_{tri['language']}"])
    result["agents"] = {"triage": tri["source"], "writer": ctx.get("writer_source", "template"),
                        "models": sorted(llm.models_used)}
    ctx.update(name=name, language=tri["language"], customer_id=customer["customer_id"] if customer else None,
               sender=email.sender)
    return _finish(result, ctx, trace, auto_approve)


def _handover(base, email, customer, tri, tools, name, trace):
    ids = _uniq(ORDER_ID_RE.findall(f"{email.subject}\n{email.latest}"))
    own = []
    for oid in ids:
        o = tools.get_order(oid)
        if o and (o.get("customer_email") or "").lower() == email.sender:
            own.append(oid)
    trigger = next((r["summary"] for r in tri["requests"] if r["type"] == "escalation"), "escalation")
    requests = [{"id": f"R{i}", "type": r["type"], "summary": r["summary"], "order_id": r.get("order_id") if r.get("order_id") in own else None,
                 "items": r.get("items", []), "outcome": "handed_over" if r["type"] == "escalation" else "deferred_to_senior_team",
                 "reason_code": "LEGAL_OR_ESCALATION"} for i, r in enumerate(tri["requests"], 1)]
    trace.log("orchestrator", "handover", trigger=trigger, orders=own)
    result = {**base, "decision": "handover", "reason_code": "LEGAL_OR_ESCALATION", "amount": None,
              "citations": _uniq(own + ["EP 1", "EP 2"]), "needs_human": True, "requests": requests, "refund": None,
              "draft_reply": handover_reply(name), "tags": ["escalation"], "priority": "P1",
              "handover_note": f"Escalation: {trigger}. Reply must not admit fault, offer compensation or discuss the case (EP 2)."}
    return result, {"points": [], "writer_source": "template"}


def _aggregate(base, resolutions, tri, tags, name, llm, trace):
    reason = min((r["reason_code"] for r in resolutions), key=REASON_ORDER.index)
    refunds = [r for r in resolutions if r["reason_code"] == "REFUND_ELIGIBLE"]
    failed = any(r["outcome"] == "tool_error" for r in resolutions)
    decision = "handover" if failed else ("reply_with_refund" if refunds else "reply")
    amount = round(sum(r["amount"] for r in refunds), 2) if refunds else None
    priority = min((r["priority"] for r in resolutions), default="P3")
    lines = [line for r in refunds for line in r["refund_lines"]]
    points = _uniq(p for r in resolutions for p in r["points"])
    reply, src = draft(name, tri["language"], points, llm, trace)
    result = {**base, "decision": decision, "reason_code": reason, "amount": amount,
              "citations": _uniq(c for r in resolutions for c in r["citations"]),
              "needs_human": decision in ("reply_with_refund", "handover"),
              "requests": [{k: r[k] for k in ("id", "type", "summary", "order_id", "items", "outcome", "reason_code",
                                               "amount", "citations", "facts")} for r in resolutions],
              "refund": {"total": amount, "currency": "INR", "lines": lines, "status": "pending_approval"} if refunds else None,
              "draft_reply": reply, "tags": _uniq(t for r in resolutions for t in r["tags"]) + [r["type"] for r in resolutions],
              "priority": priority}
    trace.log("orchestrator", "decision", decision=decision, reason_code=reason, amount=amount, priority=priority,
              needs_human=result["needs_human"])
    return result, {"points": points, "writer_source": src}


def _finish(result: dict, ctx: dict, trace: Trace, auto_approve: bool) -> dict:
    if result["needs_human"] and not auto_approve:
        result["status"] = "awaiting_approval"
        save_pending(result, ctx)
        trace.log("approval", "paused", run_id=result["run_id"],
                  hint=f"python -m app approve {result['run_id']} --action approve|reject|change")
    else:
        approver = "auto-approve" if result["needs_human"] else None
        result["status"] = "approved" if result["needs_human"] else "completed"
        if result.get("refund"):
            result["refund"]["status"] = "approved"
        commit(result, approver, trace)
    trace.log("orchestrator", "run_finished", status=result["status"], decision=result["decision"])
    return result


__all__ = ["run_case", "DataError"]
