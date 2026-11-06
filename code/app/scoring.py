"""Scores one result against an expected file, the way the brief describes: decision, reason_code (any of
the accepted codes), amount within tolerance, must_cite IDs present in citations, needs_human, and
key_numbers (when the expected file has them) present among the numbers in the result.
Each scored field counts equally; must_cite and key_numbers give partial credit."""
from __future__ import annotations

import re


def numbers_in(obj) -> list[float]:
    out: list[float] = []
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, str):
        out += [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", obj)]
    elif isinstance(obj, dict):
        for v in obj.values():
            out += numbers_in(v)
    elif isinstance(obj, list):
        for v in obj:
            out += numbers_in(v)
    return out


def score_case(result: dict, expected: dict) -> float:
    parts: list[float] = []
    parts.append(float(result.get("decision") == expected.get("decision")))

    codes = expected.get("reason_code")
    codes = codes if isinstance(codes, list) else [codes]
    parts.append(float(result.get("reason_code") in codes))

    exp_amt = expected.get("amount")
    got = result.get("amount")
    if exp_amt is None:
        parts.append(float(got is None))
    else:
        value = exp_amt["value"] if isinstance(exp_amt, dict) else float(exp_amt)
        tol = exp_amt.get("tolerance", 0.01) if isinstance(exp_amt, dict) else 0.01
        parts.append(float(isinstance(got, (int, float)) and abs(got - value) <= tol))

    must = expected.get("must_cite") or []
    cited = set(result.get("citations") or [])
    parts.append(sum(m in cited for m in must) / len(must) if must else 1.0)

    parts.append(float(bool(result.get("needs_human")) == bool(expected.get("needs_human"))))

    keys = expected.get("key_numbers")
    if keys:
        have = numbers_in(result)
        hits = 0
        for k in keys:
            v = k["value"] if isinstance(k, dict) else float(k)
            tol = k.get("tolerance", 0.01) if isinstance(k, dict) else 0.01
            hits += any(abs(h - v) <= tol for h in have)
        parts.append(hits / len(keys))
    return round(sum(parts) / len(parts), 3)
