# How I used AI coding tools

**Tool:** Claude Code (Claude Opus 5.5) in VS Code, with this repository's activity-logging settings left on.

## What it did

- **Read the pack and summarised it.** It listed what had to be built and highlighted the parts that are easy to miss: the RP 5.2 reason order, the identity rule, damage needing a photo, the two emails with injected instructions (EM-0702, EM-0866), and the thread in which the customer withdraws a refund (EM-0114).
- **Analysed the data before writing code.** It checked the five samples against `store.sqlite` to work out the rules behind the expected answers (e.g. S04's ₹2,298 = 2 × ₹1,149, and the pending shipment means the address can change). It sorted all 200 emails into templates and listed the order facts for each one.
- **Wrote the code** in `app/` (orchestrator, four agents, tools, LLM client, approval step, scoring), the test cases, the unit tests and these documents.
- **Ran it and fixed what failed.** It ran the samples and test cases with live models and with no models, sent a broken key to test the fallbacks, used a missing email and a missing data folder, and walked through approve, reject and change. It also ran all 200 emails offline and installed the requirements into a fresh virtual environment.

## What was checked and changed along the way

The score alone was not trusted: every drafted reply in the test set was read. That turned up problems the
score did not show, and each one was fixed in code, not only in a prompt:

- The writer model invented a **"store credit" offer** for a customer who was not owed a refund. Fix: the writer may only rephrase the points the policy agent settled, and code rejects any offer word (credit, voucher, replacement, compensation…) that is not in those points.
- Replies **dropped the help-article ID** that RP 4.1 says must be named. Fix: every order and article ID in the points must appear in the reply.
- The model wrote **non-breaking hyphens** ("HA‑06"). Fix: they are normalised before checking.
- Triage read **"a question about exchanges"** as a refund request and then guessed an order. Fix: a prompt rule, plus code that never guesses an order unless the customer clearly asks for money back.
- The knowledge agent turned down HA-10 for "using non-stick cookware" because triage had reworded the question. Fix: an exact match on an article title wins, and the customer's own words go to the knowledge agent for single-request emails. A first version that searched on the whole email broke the app-crash request in S04; this was caught by re-running and changed.
- In the rule-based fallback, a sentence-by-sentence rewrite made things worse (80% on the samples) and was reverted to a narrower change.

## Decisions I own

The split between models and code (no model in any money or identity decision), and the readings of policy
where it is silent (listed at the end of `README.md`), are design choices to defend in review. I checked them
against the policy text and the sample answers.
