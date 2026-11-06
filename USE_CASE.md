# Use case 4: Customer support inbox

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

*Synthetic and fictional data.*
