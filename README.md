# AZ4C assignment data pack: use case 4, Customer support inbox

This pack holds everything you need to build and test your system for this use case.

## What's inside

```
README.md        this file
USE_CASE.md      the use case on its own
COMMON.md        commands, DATA_DIR, result format, mock system
data/            store.sqlite plus one CSV per table (7 files)
docs/            company documents as Markdown (233 files)
samples/         5 test cases and their expected answers (10 files)
schema/          result.schema.json, the exact result format
```

## The use case

**Who it's for:** the support lead at Lumora Home, a fictional online store for home, kitchen and small electronics.

**The problem:** the store gets about 500 support emails a day. One email often holds two or three requests, agents miss some of them, and customers write again.

## What your system does

Takes one email, finds every request in it, sorts out each one using the order data, policies and help articles, drafts one reply, and logs a ticket in the mock helpdesk.

## How a run starts

```json
{ "case_id": "UC4-S01", "input": { "email_id": "EM-0312" } }
```

## What you get

- **Tables** in `data/store.sqlite` (also as CSV): customers, products, orders, shipments, past refunds.
- **Documents** in `docs/`:
  - `emails/`: about 200 customer emails; some are threads.
  - `help_articles/`: about 30 help articles.
  - `policies/`: the refund and escalation policies. Your system must follow them.
  - `tone_guide.md`: how replies should sound.

## What your system returns

- `decision`: `reply`, `reply_with_refund` or `handover` (to a person)
- `reason_code`: `RESOLVED`, `REFUND_ELIGIBLE`, `REFUND_NOT_ELIGIBLE`, `IDENTITY_MISMATCH`, `LEGAL_OR_ESCALATION` or `NO_SOURCE_AVAILABLE`
- `requests`: each request found, and how it was resolved
- `draft_reply`, `tags` and `priority` (P1, P2 or P3)
- `amount`: the refund amount, if any
- `citations`: order IDs, policy sections and help articles used

## Who approves (Parts B and C)

The support lead approves every refund and every handover.

## What it writes

- A ticket in `mock_system/helpdesk_tickets.jsonl`
- Approved refunds in `mock_system/refund_requests.jsonl`

## How to load the data

Unzip the pack and set `DATA_DIR` to its folder.

```python
import os, sqlite3
con = sqlite3.connect(os.path.join(os.environ["DATA_DIR"], "data", "store.sqlite"))
print(con.execute("SELECT * FROM customers WHERE email = ?", ("someone@example.com",)).fetchone())
```

The same tables are in `data/csv/` if you prefer CSV. Each email is a Markdown file in `docs/emails/` named by its email ID, with the sender, date and subject at the top; earlier messages in a thread are quoted below the latest one. Help articles are in `docs/help_articles/`, policies in `docs/policies/`.

## Samples and scoring

`samples/` holds 5 test cases. `samples/expected/` holds the expected answer for each. We score these fields: `decision`, `reason_code`, `amount` (within the tolerance shown), `key_numbers` where present (they must appear among the numbers in your result), the IDs in `must_cite` (your `citations` must include them) and `needs_human`. We run our own hidden cases the same way.

The exact result format is in `schema/result.schema.json`, and the shared conventions (commands, `DATA_DIR`, the mock system) are in `COMMON.md`.

*Synthetic and fictional data. All companies, people and figures are invented.*
