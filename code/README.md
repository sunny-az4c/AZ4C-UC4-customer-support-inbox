# Customer support inbox

Takes one Lumora Home support email, finds every request in it, settles each one with the order data,
the refund and escalation policies and the help articles, drafts one reply, and logs a ticket (plus any
approved refunds) in the mock helpdesk. The support lead approves every refund and every handover.

**Score:** 100% on the 5 samples and 100% on my 24 test cases with live models; 100% / 99.2% with no model
at all (rule-based fallbacks). Details in [Tests and score](#tests-and-score).

## Set up and run

Python 3.12. From a fresh clone:

```
cd code
python -m venv .venv && .venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                    # add GROQ_API_KEY (and GEMINI_API_KEY if you have one)

python -m app run --case ../samples/UC4-S04.json --out out/UC4-S04.json          # pauses for approval
python -m app approve <run_id> --action approve                                   # or reject / change
python -m app run --case ../samples/UC4-S04.json --out out/UC4-S04.json --auto-approve
python -m app eval --cases ../samples
python -m app eval --cases tests/cases                                            # writes tests/score.json
python -m unittest discover -s tests                                              # unit tests, no model calls
```

`DATA_DIR` defaults to the repository root (the folder that holds `data/` and `docs/`); set it to point at
another copy of the pack. Optional settings are listed, without values, in `.env.example`.

**Approval step.** A run that needs the support lead stops with `status: awaiting_approval`, saves its
state to `runs/<run_id>.json`, and writes nothing. `approve` resumes it:

| Action | Effect |
| --- | --- |
| `--action approve` | Writes the ticket and the refund request(s) |
| `--action reject` | Closes the run as rejected; writes nothing |
| `--action change --amount 1149 --note "..."` | The lead sets a different refund total and/or adds a note; the reply is updated, then everything is written |

Writes are idempotent per run, so approving twice never writes twice. `--auto-approve` approves straight away.

**What it writes** (`mock_system/` at the repository root, or `MOCK_SYSTEM_DIR`):
- `helpdesk_tickets.jsonl`: one ticket per finished run: decision, reason, priority, tags, requests, draft reply, approver.
- `refund_requests.jsonl`: one line per approved refund item: order, SKU, qty, unit price, amount, policy rule, approver.

**Result.** Every result has the required fields (`case_id`, `decision`, `reason_code`, `amount`,
`citations`, `needs_human`, `trace_id`), plus `requests` (each request with its outcome, reason, citations and
the facts behind it), `refund`, `draft_reply`, `tags`, `priority`, `run_id` and `status`. The trace of each run is
`traces/<trace_id>.jsonl`.

## Design

Diagram: [docs/architecture.md](docs/architecture.md).

The rule I designed around: **models handle language, code makes decisions.** Refund eligibility, amounts,
identity checks, reason codes and priorities come from code reading the store, so they are exact and the
same every run. The models read the email, choose help articles and write the reply, and code checks
everything they return.

| Agent | Job | Model |
| --- | --- | --- |
| **Orchestrator** (`orchestrator.py`) | Runs the steps below in order. Short-circuits to a handover on any escalation. Aggregates the requests into one decision (RP 5) and runs the approval gate | none |
| **Triage** (`agents/triage.py`) | Lists every request in the *latest* message (quoted thread text is context only) with its type, order ID, items, photo flag and new address. Code then checks the output: order IDs must appear in the email; escalation words, photo evidence and injected instructions are confirmed with patterns | gpt-oss-120b |
| **Policy** (`agents/policy.py`) | Settles each request with the store data: identity (RP 1.1), refund window and returnability (RP 2.1/2.2), damage on arrival with photo within 7 days (RP 2.3), amount = unit price × qty (RP 2.4), address change and cancellation only while pending (RP 3.1), order and refund status, warranty. Produces the reason code, citations, facts, and the only points the reply may make | none |
| **Knowledge** (`agents/knowledge.py`) | For how-to and product questions (RP 4.1): an exact match on an article title wins; otherwise BM25 finds candidates and the model decides whether one really answers the question. If none does, the reply says a specialist will answer within 2 working days | gpt-oss-120b |
| **Writer** (`agents/writer.py`) | Turns the approved points into one reply in the tone guide's voice (first name, answers in the order asked, sign-off "Team Lumora Home", light Hinglish touch when the customer used it). Handovers always use the fixed EP 2 text | gpt-oss-20b |

**How the orchestrator decides what runs next.** Intake (read the email, look up the sender) → triage →
if any request is an escalation, hand over and stop (EP 2: no case details, no promises) → otherwise the policy
agent settles each request in order, calling the knowledge agent for questions → aggregate: decision is
`reply_with_refund` if at least one refund is due, `handover` if a request could not be settled because a tool
failed, else `reply`. The main reason is the first that applies in RP 5.2's order; priority P1/P2/P3 follows
RP 5.3. `needs_human` is true for every refund and every handover → writer → approval gate → mock system.

**Safety.**
- Emails are data (EP 4). Prompts wrap the email in tags and say so.
- An order ID the model returns is dropped unless it is in the email.
- Amounts come only from the store, never from the email. EM-0866 asks "the support AI" to refund ₹50,000; the system checks the real order and declines under RP 2.1, and the ticket is tagged `instructions_in_email_ignored`.
- On an identity mismatch the reply contains no order details at all.
- The writer's draft is rejected and retried if it adds an order number, amount, date or article ID that isn't in the points; drops one that is; makes an offer the policy didn't make (credit, voucher, replacement, compensation…); or lacks the sign-off.

## Tools

All in `tools.py`. Each tool takes plain arguments, returns plain dicts, and logs its arguments, timing and a
result summary to the trace. All of them read the data pack, which is opened read-only (`mode=ro`). If
`store.sqlite` is missing, the CSVs are loaded into an in-memory database instead.

| Tool | Takes | Returns |
| --- | --- | --- |
| `read_email` | email ID (validated `EM-nnnn`) | sender, date, subject, latest message, quoted thread, attachments |
| `lookup_customer` | sender address | customer row or `None` |
| `get_order` | order ID | order + the account's email, or `None` |
| `get_order_items` | order ID | SKU, name, category, returnable, qty, unit price |
| `get_shipment` | order ID | courier, tracking number, status, dispatched/delivered dates |
| `get_refunds` | order ID | past refunds (SKU, amount, status, date) |
| `list_customer_orders` | customer ID | the customer's orders, newest first (to find an order the customer didn't number) |
| `search_help_articles` | text, k | top-k `(article_id, BM25 score)`; index built in memory from `DATA_DIR` on first use |
| `get_help_article` | article ID | title and text |

**Errors.** A tool that fails raises `ToolError`, which is logged with its arguments. A missing email
becomes a `handover` for a person. A tool failure while settling one request marks that request
`tool_error`; the whole email is then handed over rather than half-answered. A missing data pack stops the
command with a clear message.

## Models used and why

All through Groq's OpenAI-compatible API, at temperature 0, in JSON mode.

- **Triage: `openai/gpt-oss-120b`.** Finding every request, ignoring a withdrawn one in a thread, and telling a policy question from a refund request is the hardest reading in the system, so it gets the strongest model on the key. Low reasoning effort keeps it at about 1 second.
- **Knowledge: `openai/gpt-oss-120b`.** Deciding that an article does *not* answer a question (and so not guessing, RP 4.1) needs judgement.
- **Writer: `openai/gpt-oss-20b`.** Rephrasing points that are already settled is easy, and the code checks catch slips. It also runs on a separate rate-limit bucket (Groq limits tokens per minute per model), so triage and writing don't slow each other.
- **Backups.** Each agent falls back to the other gpt-oss model, then to Gemini (`gemini-2.5-flash`, only if `GEMINI_API_KEY` is set), then to its rule-based path.

There's no model in the policy step on purpose: money and identity decisions must be exact and explainable.

## Failure handling

| Failure | What happens |
| --- | --- |
| Rate limit (429) or 5xx | Waits for the time in `retry-after` / `x-ratelimit-reset-*` (or backs off exponentially) and retries, up to 4 times per model, then moves to the next model in the chain |
| Network error or timeout | Same retry and back-off, then the next model |
| Bad model reply (not JSON, wrong type, unknown article, invented order ID, draft fails the checks) | Tells the model what was wrong and retries once on the same model, then tries the next model |
| Every model fails / no key / `LLM_OFFLINE=1` | Rule-based triage, BM25-threshold knowledge, template reply. The run still finishes; the trace records each `fallback` |
| Tool error | Logged; that request becomes `tool_error` and the email is handed to a person |
| Email or order not found | Missing email → handover. Unknown order number → the reply asks the customer to check it. No order number → the system looks among the customer's orders, else asks for it |
| Missing photo for a damage claim | Within 7 days: the reply asks for a photo by a specific date (no refund yet). Later than 7 days: not eligible |

Identical model calls are cached on disk (`.cache/`, keyed by prompt, model and prompt version), so re-running
an eval costs nothing and gives the same answer. Set `LLM_CACHE=0` to turn the cache off.

## Tests and score

`tests/cases/` holds 24 cases (`UC4-T01`–`T24`), with answers in `tests/expected/`. Each expected file has a
`note` saying what the case tests. I chose them after reading all 200 emails: they fall into about 15
templates plus about 15 one-off emails that hit the policy edges, and the tests cover both:

- **Refunds:** eligible (T03 Hinglish, T07 damaged electronics with photo); outside the window (T04 electronics at 20 days, T08); already refunded (T11); not yet delivered (T13); damage reported late (T16); damage within 7 days with no photo yet (T17)
- **Identity and escalation:** spouse writing from an unregistered address (T09); consumer court / legal notice (T10)
- **Order changes:** address change after dispatch (T05); cancellation while pending (T12)
- **Injected instructions:** in a quoted reply (T14) and in the body asking for ₹50,000 (T15)
- **Thread:** the customer withdraws their refund and asks about another order (T02)
- **Questions:** answered from an article (T06, T18, T24); no article covers it (T01); warranty on non-appliances (T19, T22)
- **Plain cases:** compliment (T20), Hinglish order status (T21), courier complaint (T23)

| Run | Samples (5) | My tests (24) |
| --- | --- | --- |
| Live models | **100%** | **100%** |
| No model (`LLM_OFFLINE=1`) | 100% | 99.2% (T01: the BM25 threshold "answers" a question no article covers) |

Scoring (`scoring.py`) follows the brief. These fields count equally: decision, reason code (any accepted
one), amount within tolerance, `must_cite` (partial credit), `needs_human`, and `key_numbers` when present
(each must appear among the numbers in the result). `tests/test_unit.py` has 14 unit tests for the policy
rules, guards and scoring. I also ran all 200 emails offline with no crashes, and compared live against
offline decisions on 20 further emails.

**Choices where the policy is silent** (worth checking against the hidden cases):
- A customer offering to send a photo ("Do you want a photo?") hasn't sent one. Within 7 days the reply asks for it (`RESOLVED`); after 7 days the damage refund is `REFUND_NOT_ELIGIBLE`.
- A warranty question about a non-electronic item: HA-14 covers appliances only, so no article answers it → `NO_SOURCE_AVAILABLE`.
- Cancelling a pending prepaid order is a cancellation (`RESOLVED`, HA-03), not an RP 2 refund.
- A "refund status" question is answered (`RESOLVED`, HA-05) and not re-judged as a new refund request.
- On an identity mismatch, `citations` holds only `RP 1.1`: the other customer's order isn't cited.
