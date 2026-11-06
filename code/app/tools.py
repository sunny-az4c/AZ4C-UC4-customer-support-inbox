"""Tools the agents call. Each takes plain arguments, returns plain dicts, and logs to the trace.

A failing tool raises ToolError; the caller decides what that means for the request
(the orchestrator hands the email to a person rather than guess).
"""
from __future__ import annotations

import time

from .data import DataError, Email, Store
from .trace import Trace


class ToolError(Exception):
    pass


class Tools:
    def __init__(self, store: Store, trace: Trace):
        self.store = store
        self.trace = trace

    def _call(self, name: str, fn, *args):
        t = time.perf_counter()
        try:
            out = fn(*args)
        except DataError as e:
            self.trace.log("tools", "tool_error", tool=name, args=args, error=str(e))
            raise ToolError(f"{name}: {e}") from e
        except Exception as e:  # sqlite errors, file errors
            self.trace.log("tools", "tool_error", tool=name, args=args, error=f"{type(e).__name__}: {e}")
            raise ToolError(f"{name}: {type(e).__name__}: {e}") from e
        summary = out if not isinstance(out, list) else f"{len(out)} rows"
        if isinstance(out, Email):
            summary = {"email_id": out.email_id, "sender": out.sender, "date": str(out.date), "thread": bool(out.quoted)}
        self.trace.log("tools", "tool_call", tool=name, args=args, ms=round((time.perf_counter() - t) * 1000),
                       result=summary)
        return out

    # ---- data pack
    def read_email(self, email_id: str) -> Email:
        return self._call("read_email", self.store.email, email_id)

    def lookup_customer(self, email: str) -> dict | None:
        return self._call("lookup_customer", self.store.customer_by_email, email)

    def get_order(self, order_id: str) -> dict | None:
        return self._call("get_order", self.store.order, order_id)

    def get_order_items(self, order_id: str) -> list[dict]:
        return self._call("get_order_items", self.store.order_items, order_id)

    def get_shipment(self, order_id: str) -> dict | None:
        return self._call("get_shipment", self.store.shipment, order_id)

    def get_refunds(self, order_id: str) -> list[dict]:
        return self._call("get_refunds", self.store.refunds, order_id)

    def list_customer_orders(self, customer_id: str) -> list[dict]:
        return self._call("list_customer_orders", self.store.customer_orders, customer_id)

    def search_help_articles(self, query: str, k: int = 4) -> list[tuple[str, float]]:
        return self._call("search_help_articles", self.store.search_articles, query, k)

    def get_help_article(self, article_id: str) -> dict | None:
        return self._call("get_help_article", lambda a: self.store.articles().get(a), article_id)

    def list_help_articles(self) -> dict[str, dict]:
        return self.store.articles()
