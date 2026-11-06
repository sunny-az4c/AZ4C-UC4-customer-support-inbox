"""Read-only access to the data pack: store tables, emails and help articles.

The store is opened read-only. If store.sqlite is missing, the CSVs are loaded into an
in-memory database instead. Nothing here ever writes to DATA_DIR.
"""
from __future__ import annotations

import csv
import datetime as dt
import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

EMAIL_ID_RE = re.compile(r"^EM-\d{3,6}$")
ORDER_ID_RE = re.compile(r"\bLH-3\d{5}\b")
FOOTER = "*Synthetic and fictional data.*"
TABLES = ("customers", "products", "orders", "order_items", "shipments", "refunds")


class DataError(Exception):
    """A record or file the system needs is missing or unreadable."""


@dataclass
class Email:
    email_id: str
    sender: str
    to: str
    date: dt.date
    subject: str
    latest: str           # the customer's newest message: the only part that holds requests
    quoted: str = ""      # earlier messages in the thread, context only
    attachments: list[str] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return f"{self.subject}\n{self.latest}\n{self.quoted}"


def _header(text: str, name: str) -> str:
    m = re.search(rf"^\*\*{name}:\*\*\s*(.*)$", text, re.M)
    return m.group(1).strip() if m else ""


def parse_email(text: str, email_id: str) -> Email:
    date_s = _header(text, "Date")
    try:
        date = dt.date.fromisoformat(date_s)
    except ValueError as e:
        raise DataError(f"{email_id}: bad or missing date {date_s!r}") from e
    sender = _header(text, "From").lower()
    if not sender:
        raise DataError(f"{email_id}: no sender")
    body = text.split("**Subject:**", 1)[-1]
    body = body.split("\n", 1)[1] if "\n" in body else ""
    body = body.replace(FOOTER, "").strip()
    # Earlier messages are quoted below a '---' line, each starting with '**On <date>, <who> wrote:**'.
    parts = re.split(r"\n-{3,}\s*\n|\n(?=\*\*On \d{4}-\d{2}-\d{2},)", body, maxsplit=1)
    latest = parts[0].strip()
    quoted = parts[1].strip() if len(parts) > 1 else ""
    attachments = re.findall(r"\b[\w\-]+\.(?:jpe?g|png|heic|pdf|mp4)\b", latest, re.I)
    return Email(email_id, sender, _header(text, "To").lower(), date, _header(text, "Subject"),
                 latest, quoted, attachments)


class Store:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        db = self.data_dir / "data" / "store.sqlite"
        if db.is_file():
            self.con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        else:
            self.con = self._load_csv()
        self.con.row_factory = sqlite3.Row
        self._articles: dict[str, dict] | None = None
        self._bm25: _BM25 | None = None

    def _load_csv(self) -> sqlite3.Connection:
        con = sqlite3.connect(":memory:")
        for table in TABLES:
            path = self.data_dir / "data" / "csv" / f"{table}.csv"
            if not path.is_file():
                raise DataError(f"store not found: neither store.sqlite nor {path.name} exists under {self.data_dir}")
            with path.open(encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
            cols = list(rows[0].keys()) if rows else []
            con.execute(f"CREATE TABLE {table} ({', '.join(cols)})")
            con.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' * len(cols))})",
                            [[_num(r[c]) for c in cols] for r in rows])
        return con

    def _one(self, sql: str, args: tuple) -> dict | None:
        row = self.con.execute(sql, args).fetchone()
        return dict(row) if row else None

    def _all(self, sql: str, args: tuple) -> list[dict]:
        return [dict(r) for r in self.con.execute(sql, args).fetchall()]

    # ---- tables
    def customer_by_email(self, email: str) -> dict | None:
        return self._one("SELECT customer_id, name, email, city FROM customers WHERE lower(email) = lower(?)", (email,))

    def order(self, order_id: str) -> dict | None:
        return self._one(
            "SELECT o.order_id, o.customer_id, o.order_date, o.status, o.payment_method, c.email AS customer_email "
            "FROM orders o LEFT JOIN customers c USING (customer_id) WHERE o.order_id = ?", (order_id,))

    def order_items(self, order_id: str) -> list[dict]:
        return self._all(
            "SELECT i.sku, p.name, p.category, p.returnable, i.qty, i.unit_price FROM order_items i "
            "LEFT JOIN products p USING (sku) WHERE i.order_id = ? ORDER BY i.rowid", (order_id,))

    def shipment(self, order_id: str) -> dict | None:
        return self._one("SELECT courier, tracking_no, status, dispatched_on, delivered_on FROM shipments WHERE order_id = ?",
                         (order_id,))

    def refunds(self, order_id: str) -> list[dict]:
        return self._all("SELECT refund_id, sku, amount, status, created_on FROM refunds WHERE order_id = ?", (order_id,))

    def customer_orders(self, customer_id: str) -> list[dict]:
        return self._all("SELECT order_id, order_date, status FROM orders WHERE customer_id = ? ORDER BY order_date DESC",
                         (customer_id,))

    # ---- documents
    def email(self, email_id: str) -> Email:
        if not EMAIL_ID_RE.match(email_id or ""):
            raise DataError(f"not a valid email ID: {email_id!r}")
        path = self.data_dir / "docs" / "emails" / f"{email_id}.md"
        if not path.is_file():
            raise DataError(f"email {email_id} not found")
        return parse_email(path.read_text(encoding="utf-8"), email_id)

    def articles(self) -> dict[str, dict]:
        if self._articles is None:
            arts = {}
            for path in sorted((self.data_dir / "docs" / "help_articles").glob("HA-*.md")):
                text = path.read_text(encoding="utf-8").replace(FOOTER, "").strip()
                first, _, rest = text.partition("\n")
                title = re.sub(r"^#\s*HA-\d+:\s*", "", first).strip()
                arts[path.stem] = {"id": path.stem, "title": title, "body": rest.strip()}
            self._articles = arts
        return self._articles

    def search_articles(self, query: str, k: int = 4) -> list[tuple[str, float]]:
        """BM25 over title + body, built in memory on first use."""
        if self._bm25 is None:
            arts = self.articles()
            self._bm25 = _BM25({a: f"{v['title']} {v['title']} {v['body']}" for a, v in arts.items()})
        return self._bm25.search(query, k)


def _num(v: str):
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v if v != "" else None


_STOP = set("a an the and or of to in on for my your is it i me we you our can how do does what when will with "
            "this that be are was from at by any about have has hi hello please thanks question quick help".split())


def tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [w[:-1] if len(w) > 4 and w.endswith("s") else w for w in words if w not in _STOP]


class _BM25:
    def __init__(self, docs: dict[str, str], k1: float = 1.4, b: float = 0.75):
        self.ids = list(docs)
        self.tf = [Counter(tokens(docs[i])) for i in self.ids]
        self.len = [sum(t.values()) for t in self.tf]
        self.avg = sum(self.len) / max(len(self.len), 1)
        df = Counter(w for t in self.tf for w in t)
        n = len(self.ids)
        self.idf = {w: math.log(1 + (n - d + 0.5) / (d + 0.5)) for w, d in df.items()}
        self.k1, self.b = k1, b

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        q = tokens(query)
        scores = []
        for i, tf in enumerate(self.tf):
            s = 0.0
            for w in q:
                f = tf.get(w, 0)
                if f:
                    s += self.idf[w] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
            scores.append((self.ids[i], round(s, 3)))
        scores.sort(key=lambda x: -x[1])
        return [s for s in scores[:k] if s[1] > 0]
