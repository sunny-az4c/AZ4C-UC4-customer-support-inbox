# Architecture

One email in, one decision, one reply, one ticket out. Models read and write language; code makes every
policy decision.

```mermaid
flowchart TD
    CASE["case.json<br/>{email_id}"] --> ORCH

    subgraph ORCH["Orchestrator (app/orchestrator.py)"]
        direction TB
        INTAKE["1. Intake<br/>read_email, lookup_customer<br/>split latest message from quoted thread"]
        TRIAGE["2. Triage agent<br/>gpt-oss-120b, JSON<br/>+ code checks: order IDs, photo, escalation, injection"]
        ESC{"Escalation?<br/>(EP 1)"}
        POLICY["3. Policy agent (code only)<br/>per request: identity RP 1.1, refunds RP 2,<br/>changes RP 3, warranty, status"]
        KNOW["Knowledge agent<br/>title match / BM25 + gpt-oss-120b<br/>(questions only, RP 4)"]
        AGG["4. Aggregate (RP 5)<br/>decision, main reason, amount,<br/>priority, citations, needs_human"]
        WRITER["5. Writer agent<br/>gpt-oss-20b, rephrases approved points<br/>+ code checks: IDs, numbers, offers, sign-off"]
        HANDOVER["Handover<br/>fixed EP 2 reply, P1"]
        GATE{"needs_human?<br/>(refund or handover)"}
    end

    INTAKE --> TRIAGE --> ESC
    ESC -- yes --> HANDOVER --> GATE
    ESC -- no --> POLICY
    POLICY <--> KNOW
    POLICY --> AGG --> WRITER --> GATE

    GATE -- "no" --> WRITE
    GATE -- "yes + --auto-approve" --> WRITE
    GATE -- "yes" --> PAUSE["runs/RUN-id.json<br/>status: awaiting_approval"]
    PAUSE -- "approve / change" --> WRITE
    PAUSE -- "reject" --> NOTHING["nothing written"]

    WRITE["Mock system<br/>mock_system/helpdesk_tickets.jsonl<br/>mock_system/refund_requests.jsonl"]

    DATA[("Data pack (read-only)<br/>store.sqlite, emails,<br/>help articles, policies")] -.tools.-> INTAKE
    DATA -.tools.-> POLICY
    DATA -.tools.-> KNOW
    ORCH -.every step.-> TRACE["traces/TR-id.jsonl"]
```

## Fallbacks (each agent keeps working without a model)

| Step | Normal path | If every model call fails |
| --- | --- | --- |
| Triage | gpt-oss-120b, then gpt-oss-20b, then Gemini (if a key is set) | Rule-based extractor (patterns per request type) |
| Knowledge | exact title match, else BM25 + gpt-oss-120b picks among candidates | BM25 with a strict score threshold; below it, "a specialist will reply" |
| Policy | code only | (not model-dependent) |
| Writer | gpt-oss-20b, then gpt-oss-120b, then Gemini | Template reply built from the same approved points |
| Handover | fixed template | (not model-dependent) |

A sample trace of UC4-S04 is in [sample_trace_UC4-S04.jsonl](sample_trace_UC4-S04.jsonl).
