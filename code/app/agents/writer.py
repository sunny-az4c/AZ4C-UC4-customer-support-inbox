"""Writer agent: turns the settled points into one reply in Lumora Home's tone.

The model may only rephrase the points it is given. Code checks the draft: no order IDs, amounts,
dates or article IDs that are not in the points, the sign-off is there, and a handover makes no
promise. A draft that fails twice is replaced by the template reply built from the same points.
Handovers always use the fixed template (EP 2): no model involved.
"""
from __future__ import annotations

import re

from .. import config
from ..llm import LLM, InvalidReply, LLMError
from ..trace import Trace

SIGN_OFF = "Team Lumora Home"

SYSTEM = """You write email replies for Lumora Home, an online home and kitchen store.
Tone guide: use the customer's first name; warm and plain; answer every point in the order given;
short paragraphs; no jargon, no blame; when we can't do something, say so clearly, say why in one line, and say
what we can do instead; sign off exactly as "Team Lumora Home".
{lang}
Hard rules:
- Use ONLY the facts in the points. Do not add any order number, amount, date, timeline, article, offer or
  promise that is not in the points (no store credit, vouchers, discounts, replacements or compensation unless a
  point says so). Do not invent problems the customer did not mention.
- Keep every number exactly as written, and keep every order number (LH-...) and help article ID (HA-xx) from
  the points in the reply, e.g. "(see our help article HA-07: Descaling your Lumora electric kettle)".
- Keep it under 220 words. No subject line, no placeholders.
Reply with only JSON: {{"reply": "the full email text"}}"""

HINGLISH = "The customer wrote in Hinglish: reply in simple English with a light, friendly Hinglish touch (e.g. 'Namaste')."


def _numbers(text: str) -> set[str]:
    out = set()
    for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        n = re.sub(r"\.0+$", "", n.replace(",", ""))
        if len(n) >= 3:
            out.add(n)
    return out


def template_reply(name: str, points: list[str], language: str = "english") -> str:
    hello = f"Namaste {name}," if language == "hinglish" else f"Hi {name},"
    body = "\n\n".join(points)
    return f"{hello}\n\nThank you for writing to us.\n\n{body}\n\nWarm regards,\n{SIGN_OFF}"


def handover_reply(name: str) -> str:
    return (f"Hi {name},\n\nThank you for your email. We have received it, and a senior member of our team will "
            f"respond to you within one working day.\n\nWarm regards,\n{SIGN_OFF}")


# Offers the writer must never add on its own: they are only allowed if a point already makes them.
OFFER_WORDS = ["store credit", "credit note", "voucher", "coupon", "discount", "compensat", "gift card", "cashback",
               "replacement", "free of charge", "goodwill", "waive", "exception", "escalat", "priority", "refund"]


def _ids(text: str) -> set[str]:
    return set(re.findall(r"\b(?:LH-\d{6}|HA-\d{2}|TRK\d+)\b", text))


def draft(name: str, language: str, points: list[str], llm: LLM, trace: Trace) -> tuple[str, str]:
    """Return (reply, source) where source is 'llm' or 'template'."""
    joined = " ".join(points)
    allowed_ids = _ids(joined)
    must_name = {i for i in allowed_ids if i.startswith(("LH-", "HA-"))}
    allowed_nums = _numbers(joined)
    banned = [w for w in OFFER_WORDS if w not in joined.lower()]

    def validate(d: dict) -> dict:
        reply = str(d.get("reply") or "").strip()
        reply = re.sub("[‐‑‒]", "-", reply).replace(" ", " ")
        if len(reply) < 40:
            raise InvalidReply("reply is missing or too short")
        if SIGN_OFF not in reply[-80:]:
            raise InvalidReply(f"reply must end with the sign-off '{SIGN_OFF}'")
        extra_ids = _ids(reply) - allowed_ids
        if extra_ids:
            raise InvalidReply(f"reply mentions IDs that are not in the points: {sorted(extra_ids)}")
        missing = must_name - _ids(reply)
        if missing:
            raise InvalidReply(f"reply must keep these order numbers and help article IDs: {sorted(missing)}")
        added = [w for w in banned if w in reply.lower()]
        if added:
            raise InvalidReply(f"reply offers or mentions something that is not in the points: {added}")
        extra_nums = _numbers(reply) - allowed_nums
        if extra_nums:
            raise InvalidReply(f"reply has numbers that are not in the points: {sorted(extra_nums)}")
        if re.search(r"\[(?:name|order|amount)[^\]]*\]|\{\w+\}", reply, re.I):
            raise InvalidReply("reply has a placeholder")
        if len(reply) > 2200:
            raise InvalidReply("reply is too long")
        return {"reply": reply}

    user = f"Customer first name: {name}\nPoints to cover, in this order:\n" + \
           "\n".join(f"{i + 1}. {p}" for i, p in enumerate(points))
    try:
        out = llm.json_call("writer", config.WRITER_MODEL,
                            SYSTEM.format(lang=HINGLISH if language == "hinglish" else ""), user, validate, 900)
        trace.log("writer", "draft_ready", source="llm", chars=len(out["reply"]))
        return out["reply"], "llm"
    except LLMError as e:
        trace.log("writer", "fallback", reason=str(e))
        return template_reply(name, points, language), "template"
