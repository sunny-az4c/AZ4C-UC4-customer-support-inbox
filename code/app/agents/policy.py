"""Policy agent: settles each request with the store data and the written policies. No model here.

Every outcome is computed in code so that amounts, dates and eligibility are exact and repeatable.
Each resolution carries the reason code, the citations it rests on, the facts behind it, and the
customer-facing points the writer is allowed to say (and nothing else).
"""
from __future__ import annotations

import datetime as dt
import re

from ..data import Email
from ..llm import LLM
from ..tools import Tools
from ..trace import Trace
from .knowledge import find_answer

ELECTRONICS = "electronics"
WINDOW_DAYS = {"default": 30, ELECTRONICS: 14}   # RP 2.1
DAMAGE_DAYS = 7                                  # RP 2.3
WARRANTY_DAYS = 365                              # HA-14
PREPAID = {"card", "upi", "net banking", "netbanking", "wallet"}

SPECIALIST = "A product specialist will reply to you within 2 working days with an answer."
IDENTITY_POINT = ("To protect your account, we can only discuss an order with the account holder, from the email "
                  "address registered on that account. Please write to us from your registered email address and "
                  "we'll be glad to help.")


def rupees(x: float) -> str:
    return f"₹{x:,.2f}"


def _d(s) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(s)) if s else None
    except ValueError:
        return None


def _norm(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9.]+", (s or "").lower().replace("(", " ").replace(")", " "))) - {"the", "my", "set", "of"}


def match_items(names: list[str], items: list[dict]) -> list[dict]:
    """Order items the customer named. Empty names -> every item. Unmatched names are ignored."""
    if not names:
        return items
    chosen = []
    for name in names:
        want = _norm(name)
        if not want:
            continue
        best, best_score = None, 0.0
        for it in items:
            have = _norm(it.get("name") or "")
            score = len(want & have) / len(want) + 0.01 * len(want & have) / max(len(have), 1)
            if score > best_score:
                best, best_score = it, score
        if best is not None and best_score >= 0.6 and best not in chosen:
            chosen.append(best)
    return chosen


class Resolver:
    def __init__(self, email: Email, customer: dict | None, tools: Tools, llm: LLM, trace: Trace, n_requests: int = 1):
        self.email, self.customer, self.tools, self.llm, self.trace = email, customer, tools, llm, trace
        self.n_requests = n_requests

    # ------------------------------------------------------------------ entry point
    def resolve(self, n: int, req: dict) -> dict:
        res = {"id": f"R{n}", "type": req["type"], "summary": req["summary"], "order_id": req.get("order_id"),
               "items": req.get("items", []), "outcome": "", "reason_code": "RESOLVED", "citations": [],
               "amount": None, "refund_lines": [], "facts": {}, "points": [], "tags": [], "priority": "P3"}
        handler = getattr(self, f"_{req['type']}", self._question)
        if req["type"] in ORDER_NEEDED:
            order = self._order_for(req, res)
            if order is None:
                self._log(res)
                return res
            handler(req, res, order)
        else:
            handler(req, res)
        self._log(res)
        return res

    def _log(self, res: dict) -> None:
        self.trace.log("policy", "resolved", **{k: res[k] for k in
                       ("id", "type", "order_id", "outcome", "reason_code", "amount", "citations")}, facts=res["facts"])

    # ------------------------------------------------------------------ identity and order lookup (RP 1.1)
    def _order_for(self, req: dict, res: dict) -> dict | None:
        oid = req.get("order_id")
        if oid is None:
            if self.customer is None:
                return self._identity_fail(res, "sender is not a registered customer")
            oid = self._infer_order(req)
            if oid is None:
                res.update(outcome="order_id_needed")
                res["points"].append("Could you reply with your order number (it starts with LH-)? "
                                     "We'll look into it straight away.")
                return None
            res["order_id"] = oid
            res["facts"]["order_inferred_from_account"] = True
        order = self.tools.get_order(oid)
        if order is None:
            res.update(outcome="order_not_found")
            res["points"].append(f"We couldn't find an order with the number {oid}. Could you check the number "
                                 "and reply to this email?")
            return None
        if (order.get("customer_email") or "").lower() != self.email.sender:
            return self._identity_fail(res, "order belongs to a different account" if self.customer
                                       else "sender is not a registered customer")
        order["items"] = self.tools.get_order_items(oid)
        order["shipment"] = self.tools.get_shipment(oid) or {}
        order["refunds"] = self.tools.get_refunds(oid)
        res["citations"].append(oid)
        return order

    def _identity_fail(self, res: dict, why: str) -> None:
        res.update(outcome="identity_mismatch", reason_code="IDENTITY_MISMATCH")
        res["facts"] = {"identity_check": why}       # deliberately no order details
        res["citations"] = ["RP 1.1"]
        res["points"] = [IDENTITY_POINT]
        res["tags"].append("identity_mismatch")
        res["order_id"] = None
        return None

    def _infer_order(self, req: dict) -> str | None:
        if req["type"] == "refund_request" and not req.get("wants_refund"):
            return None           # never guess an order for something the customer did not clearly ask
        orders = self.tools.list_customer_orders(self.customer["customer_id"])
        if req.get("items"):
            hits = [o["order_id"] for o in orders if match_items(req["items"], self.tools.get_order_items(o["order_id"]))]
            if len(hits) == 1:
                return hits[0]
            if hits and req["type"] in ("damaged_item", "refund_request", "warranty"):
                return hits[0]          # most recent order holding that product
        if req["type"] == "order_status":
            open_ = [o["order_id"] for o in orders if o["status"] in ("confirmed", "shipped")]
            if len(open_) == 1:
                return open_[0]
        return orders[0]["order_id"] if len(orders) == 1 else None

    # ------------------------------------------------------------------ refunds (RP 2)
    def _check_item(self, item: dict, order: dict, damaged: bool, photo: bool) -> dict:
        ship = order["shipment"]
        delivered = _d(ship.get("delivered_on")) if ship.get("status") == "delivered" else None
        line = {"sku": item["sku"], "name": item["name"], "category": item["category"], "qty": item["qty"],
                "unit_price": item["unit_price"], "amount": round(item["unit_price"] * item["qty"], 2)}
        if delivered is None:
            return {**line, "eligible": False, "rule": "RP 2.1", "why": "not_delivered"}
        days = (self.email.date - delivered).days                  # RP 2.2
        line.update(delivered_on=str(delivered), days_since_delivery=days)
        if any(r["sku"] == item["sku"] and r["status"] != "rejected" for r in order["refunds"]):
            r = next(r for r in order["refunds"] if r["sku"] == item["sku"])
            return {**line, "eligible": False, "rule": "RP 2.1", "why": "already_refunded",
                    "refund_on": r["created_on"], "refund_amount": r["amount"]}
        if damaged:
            if days <= DAMAGE_DAYS and photo:
                return {**line, "eligible": True, "rule": "RP 2.3", "why": "damaged_on_arrival"}
            if days <= DAMAGE_DAYS:
                return {**line, "eligible": False, "rule": "RP 2.3", "why": "photo_needed",
                        "photo_deadline": str(delivered + dt.timedelta(days=DAMAGE_DAYS))}
            return {**line, "eligible": False, "rule": "RP 2.3", "why": "damage_reported_late"}
        if not item.get("returnable"):
            return {**line, "eligible": False, "rule": "RP 2.1", "why": "not_returnable"}
        window = WINDOW_DAYS[ELECTRONICS] if item["category"] == ELECTRONICS else WINDOW_DAYS["default"]
        line["window_days"] = window
        if days > window:
            return {**line, "eligible": False, "rule": "RP 2.1", "why": "outside_window"}
        return {**line, "eligible": True, "rule": "RP 2.1", "why": "within_window"}

    def _refund(self, req: dict, res: dict, order: dict, damaged: bool) -> None:
        items = match_items(req.get("items", []), order["items"])
        if not items:
            res.update(outcome="item_not_in_order")
            res["points"].append(f"We couldn't find {', '.join(req['items'])} in order {order['order_id']}. "
                                 "Could you tell us which item you mean?")
            return
        checks = [self._check_item(i, order, damaged, req.get("photo_attached", False)) for i in items]
        res["facts"].update(order_status=order["status"], shipment_status=order["shipment"].get("status"),
                            payment_method=order["payment_method"], lines=checks)
        res["tags"].append("damaged" if damaged else "return")
        res["priority"] = "P2"
        if damaged:
            res["citations"].append("HA-06")
        ok = [c for c in checks if c["eligible"]]
        for c in checks:
            if c["rule"] not in res["citations"]:
                res["citations"].append(c["rule"])
        if ok:
            total = round(sum(c["amount"] for c in ok), 2)
            res.update(outcome="refund_eligible", reason_code="REFUND_ELIGIBLE", amount=total,
                       refund_lines=[{"order_id": order["order_id"], **{k: c[k] for k in
                                      ("sku", "name", "qty", "unit_price", "amount", "rule")}} for c in ok])
            names = ", ".join(f"{c['name']} (x{c['qty']})" if c["qty"] > 1 else c["name"] for c in ok)
            how = ("to the bank account you share with us" if order["payment_method"].lower() == "cash on delivery"
                   else f"to your original payment method ({order['payment_method']})")
            pickup = "" if damaged else " We'll arrange a pickup within 3 working days; please keep it unused in its original packaging (HA-04)."
            res["points"].append(f"Your refund of {rupees(total)} for the {names} from order {order['order_id']} has "
                                 f"been approved. It will go back {how}; UPI and card refunds take 5–7 working days, "
                                 f"net banking 7–10 (HA-05).{pickup}")
            res["citations"] += [c for c in (["HA-04"] if not damaged else []) + ["HA-05"] if c not in res["citations"]]
        if any(c["why"] == "photo_needed" for c in checks):
            c = next(c for c in checks if c["why"] == "photo_needed")
            res["points"].append(f"We're sorry the {c['name']} arrived damaged. Please reply with a photo of the item "
                                 f"and its packaging by {c['photo_deadline']} (within 7 days of delivery) and we'll "
                                 "arrange a refund or replacement (HA-06).")
            if not ok:
                res.update(outcome="photo_needed", reason_code="RESOLVED")
                res["tags"].append("awaiting_photo")
        bad = [c for c in checks if not c["eligible"] and c["why"] != "photo_needed"]
        for c in bad:
            res["points"].append(self._not_eligible_point(c, order))
        if bad and not ok and res["outcome"] != "photo_needed":
            res.update(outcome="refund_not_eligible", reason_code="REFUND_NOT_ELIGIBLE")
            if any(c["category"] == ELECTRONICS and c["why"] in ("outside_window", "damage_reported_late") for c in bad):
                res["citations"].append("HA-14")
            res["citations"].append("RP 2.5")

    def _not_eligible_point(self, c: dict, order: dict) -> str:
        name, oid = c["name"], order["order_id"]
        why = c["why"]
        if why == "not_delivered":
            return (f"Order {oid} hasn't been delivered yet, so we can't refund the {name} at this stage. Once it "
                    f"arrives, you can return it within {WINDOW_DAYS[ELECTRONICS] if c['category'] == ELECTRONICS else 30} "
                    "days of delivery if it's unused and in its original packaging (HA-04).")
        if why == "already_refunded":
            return (f"The {name} from order {oid} has already been refunded ({rupees(c['refund_amount'])}, "
                    f"processed on {c['refund_on']}), so there's nothing more to refund. Refunds take 5–7 working "
                    "days to reach UPI and cards (HA-05).")
        if why == "not_returnable":
            return (f"We're unable to refund the {name}: for hygiene reasons it can't be returned once delivered. "
                    "If it arrived damaged, send us a photo within 7 days of delivery and we can help.")
        if why == "outside_window":
            extra = ""
            if c["category"] == ELECTRONICS:
                extra = (" What we can do: it's covered by a 1-year manufacturer warranty for manufacturing defects. "
                         "You can raise a warranty claim from My Orders with the serial number (HA-14).")
            return (f"We can't refund the {name} from order {oid}: it was delivered on {c['delivered_on']}, "
                    f"{c['days_since_delivery']} days ago, and {'electronics' if c['category'] == ELECTRONICS else 'items'} "
                    f"can be returned within {c['window_days']} days of delivery.{extra}")
        if why == "damage_reported_late":
            extra = ""
            if c["category"] == ELECTRONICS:
                extra = (" If it's a manufacturing defect, it's covered by the 1-year warranty: raise a claim from "
                         "My Orders with the serial number (HA-14).")
            return (f"We're sorry the {name} isn't in good shape. Damage needs to be reported within 7 days of "
                    f"delivery with a photo, and order {oid} was delivered on {c['delivered_on']} "
                    f"({c['days_since_delivery']} days ago), so we can't offer a damage refund.{extra}")
        return f"We're unable to refund the {name} from order {oid} under our refund policy."

    # ------------------------------------------------------------------ handlers
    def _refund_request(self, req, res, order):
        self._refund(req, res, order, damaged=False)

    def _damaged_item(self, req, res, order):
        self._refund(req, res, order, damaged=True)

    def _order_status(self, req, res, order):
        s, oid = order["shipment"], order["order_id"]
        st = s.get("status")
        res["facts"].update(order_status=order["status"], shipment=s)
        if st == "pending":
            res["outcome"] = "status_pending"
            res["points"].append(f"Your order {oid} is being packed and hasn't been dispatched yet. Once it's "
                                 "dispatched, delivery takes 2–4 days in metro cities and 4–7 days elsewhere (HA-23).")
            res["citations"].append("HA-23")
        elif st == "dispatched":
            res["outcome"] = "status_in_transit"
            res["points"].append(f"Your order {oid} was dispatched on {s.get('dispatched_on')} with {s.get('courier')} "
                                 f"(tracking number {s.get('tracking_no')}) and is on its way. You can follow it in "
                                 "My Orders > Track; couriers update tracking within 24 hours of pickup (HA-01). "
                                 "Delivery usually takes 2–4 days in metro cities and 4–7 days elsewhere (HA-23).")
            res["citations"] += ["HA-01", "HA-23"]
        elif st == "delivered":
            res["outcome"] = "status_delivered"
            res["points"].append(f"Our records show order {oid} was delivered by {s.get('courier')} on "
                                 f"{s.get('delivered_on')} (tracking number {s.get('tracking_no')}). If you haven't "
                                 "received it, please reply and we'll raise it with the courier right away.")
            res["citations"].append("HA-01")
        elif st == "returned":
            res["outcome"] = "status_returned"
            res["priority"] = "P2"
            res["tags"].append("failed_delivery")
            res["points"].append(f"Order {oid} was returned to us by the courier and was not delivered. Please reply "
                                 "if you'd like us to help you place it again.")
        else:
            res["outcome"] = "status_unknown"
            res["points"].append(f"We're checking the latest status of order {oid} with the courier and will update you.")

    def _refund_status(self, req, res, order):
        oid = order["order_id"]
        res["priority"] = "P2"
        res["tags"].append("refund_status")
        res["facts"]["refunds"] = order["refunds"]
        res["outcome"] = "refund_status_given"
        how = ("to the bank account you share with us (cash-on-delivery orders)"
               if order["payment_method"].lower() == "cash on delivery"
               else "to your original payment method: 5–7 working days for UPI and cards, 7–10 for net banking")
        if order["refunds"]:
            for r in order["refunds"]:
                name = next((i["name"] for i in order["items"] if i["sku"] == r["sku"]), r["sku"])
                res["points"].append(f"The refund of {rupees(r['amount'])} for the {name} from order {oid} is "
                                     f"{r['status']} (raised on {r['created_on']}). Refunds go back {how} (HA-05).")
        else:
            res["points"].append(f"We don't see the returned item from order {oid} at our warehouse yet, so the refund "
                                 f"hasn't been raised. As soon as it reaches us, the refund goes back {how} (HA-05).")
        res["citations"].append("HA-05")

    def _cancel_order(self, req, res, order):
        oid, s = order["order_id"], order["shipment"]
        res["facts"].update(shipment_status=s.get("status"), payment_method=order["payment_method"])
        res["citations"] += ["RP 3.1", "HA-03"]
        if s.get("status") == "pending":
            res["outcome"] = "cancelled"
            res["tags"].append("cancellation")
            paid = order["payment_method"].lower() in PREPAID
            res["points"].append(f"Your order {oid} hasn't been dispatched yet, so we've cancelled it free of charge."
                                 + (f" As it was paid by {order['payment_method']}, the amount goes back to the same "
                                    "payment method within 5–7 working days (HA-03)." if paid else ""))
        else:
            res["outcome"] = "cancel_not_possible"
            res["points"].append(f"Order {oid} has already been dispatched, so it can no longer be cancelled (orders "
                                 "can be cancelled only until dispatch, HA-03). Once it arrives, you can return it if "
                                 "it's unused and in its original packaging (HA-04).")
            res["citations"].append("HA-04")

    def _change_address(self, req, res, order):
        oid, s = order["order_id"], order["shipment"]
        res["facts"].update(shipment_status=s.get("status"), new_address=req.get("new_address"))
        res["citations"] += ["RP 3.1", "HA-02"]
        if s.get("status") == "pending":
            res["outcome"] = "address_changed"
            res["tags"].append("address_change")
            addr = f" to {req['new_address']}" if req.get("new_address") else ""
            res["points"].append(f"Your order {oid} hasn't been dispatched yet, so we've updated the delivery "
                                 f"address{addr} (HA-02).")
        else:
            res["outcome"] = "address_locked"
            res["points"].append(f"Order {oid} has already been dispatched, so we can't change its delivery address. "
                                 "What you can do: ask the courier to hold the parcel at their nearest hub and collect "
                                 f"it from there{' (' + s.get('courier') + ', tracking ' + s.get('tracking_no') + ')' if s.get('courier') else ''} (HA-02).")

    def _warranty(self, req, res, order):
        items = match_items(req.get("items", []), order["items"]) or order["items"]
        res["facts"]["items"] = [{k: i[k] for k in ("name", "category")} for i in items]
        elec = [i for i in items if i["category"] == ELECTRONICS]
        delivered = _d(order["shipment"].get("delivered_on"))
        if elec:
            age = (self.email.date - delivered).days if delivered else 0
            res["facts"]["days_since_delivery"] = age
            res["citations"].append("HA-14")
            if age <= WARRANTY_DAYS:
                res["outcome"] = "warranty_covered"
                res["points"].append(f"Yes: the {elec[0]['name']} carries a 1-year manufacturer warranty from the "
                                     "delivery date, covering manufacturing defects (not physical damage or misuse). "
                                     "Please raise a warranty claim from My Orders with the serial number (HA-14).")
            else:
                res["outcome"] = "warranty_expired"
                res["points"].append(f"The 1-year manufacturer warranty on the {elec[0]['name']} has ended, as it was "
                                     f"delivered on {delivered} (HA-14).")
        else:
            # HA-14 covers appliances only; nothing in our articles covers warranty on other items (RP 4.1).
            res.update(outcome="no_source", reason_code="NO_SOURCE_AVAILABLE")
            res["citations"].append("RP 4.1")
            res["tags"].append("specialist_followup")
            res["points"].append(f"Thank you for letting us know about the problem with your "
                                 f"{items[0]['name'] if items else 'item'}. {SPECIALIST}")

    def _complaint(self, req, res, order):
        res["outcome"] = "complaint_logged"
        res["tags"].append("service_feedback")
        res["points"].append(f"We're really sorry about your experience with order {order['order_id']}. That's not "
                             "the service we want you to have, and we've logged your feedback for our delivery partner.")

    def _compliment(self, req, res):
        res["outcome"] = "thanked"
        res["tags"].append("compliment")
        res["points"].append("Thank you so much for your kind words. We're delighted you're happy, and we'll share "
                             "your note with the team.")

    def _question(self, req, res):
        q = req.get("question") or req.get("summary") or self.email.latest
        if req.get("items"):
            q = f"{q} (product: {', '.join(req['items'])})"
        # The customer's own words help only when this is their one request; otherwise they blur the search.
        context = f"{self.email.subject}\n{self.email.latest}"[:600] if self.n_requests == 1 else ""
        ans = find_answer(q, self.tools, self.llm, self.trace, context)
        res["facts"]["question"] = q
        if ans["article_id"]:
            art = self.tools.get_help_article(ans["article_id"])
            res["outcome"] = "answered"
            res["citations"].append(ans["article_id"])
            res["facts"]["article"] = {"id": art["id"], "title": art["title"], "text": art["body"]}
            answer = ans["answer"].rstrip()
            answer += "" if answer.endswith((".", "!", "?")) else "."
            res["points"].append(f"{answer} You'll find this in our help article {art['id']}: {art['title']}.")
        else:
            res.update(outcome="no_source", reason_code="NO_SOURCE_AVAILABLE")
            res["citations"].append("RP 4.1")
            res["tags"].append("specialist_followup")
            res["points"].append(f"Thank you for your question. {SPECIALIST}")

    _other = _question


ORDER_NEEDED = {"order_status", "refund_request", "damaged_item", "refund_status", "cancel_order", "change_address",
                "warranty", "complaint"}
