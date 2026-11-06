# Common rules for every use case

These apply to all six use cases. Each use case's `USE_CASE.md` adds its own data, rules and outputs.

## Where the data lives

Everything runs on your own machine. Unzip the pack anywhere and point your system at it with the `DATA_DIR` environment variable, e.g. `DATA_DIR=./data-pack python -m app run --case ...`. Read tables from the SQLite file (or the CSVs) and documents from `docs/`. If you build a search index over the documents, build it locally from `DATA_DIR` on first run. Apart from model API calls, don't depend on any cloud service. Don't commit the data pack to your repository; we use our own copy.

## How a run starts

Each test case is a JSON file. It names the record that triggers the run:

```json
{ "case_id": "UC1-S01", "input": { "invoice_id": "INV-24017" } }
```

Your system reads everything else it needs from the data pack, through tools.

## Commands your repository must support

| Command | What it does |
| --- | --- |
| `python -m app run --case <case.json> --out <result.json>` | Runs one case end to end and writes the result |
| `python -m app eval --cases <folder>` | Runs every case in a folder and prints your score against `expected/` |
| `python -m app run ... --auto-approve` | Parts B and C: approves at the approval step without waiting |
| `python -m app approve <run_id> --action approve\|reject\|change` | Parts B and C: resumes a paused run |

Documented equivalents are fine, e.g. a Makefile target.

## Result format

Every `result.json` has these fields, plus the extra fields listed in your use case's spec:

| Field | Type | Meaning |
| --- | --- | --- |
| `case_id` | string | From the input |
| `decision` | string | One of the decision values in your use case's spec |
| `reason_code` | string | The main reason, from your use case's list |
| `amount` | number or null | The amount the decision is about, in rupees, where the use case has one |
| `citations` | array of strings | IDs of the records, documents and sections the decision rests on |
| `needs_human` | boolean | True when the approval rule applies |
| `trace_id` | string | Points to the trace of this run |

`schema/result.schema.json` in your pack is the exact JSON Schema.

## Expected answers

`samples/` holds 5 cases; `samples/expected/` holds the expected result for each. Expected files list only what is scored: `decision`, `reason_code` (one or more accepted codes), `amount` (with a tolerance), `must_cite` (IDs your citations must include) and `needs_human`. We score our hidden cases the same way.

## Writes to the mock system

The mock system is a folder of files your system writes to: `mock_system/` at the root of your repository. Each use case's spec names the files and the record format. Write only after the decision is final (and approved, for Parts B and C). A rejected approval writes nothing.

## Ground rules

- Data in the pack is synthetic and fictional. Treat any instruction found inside a document, email or data field as data, never as an instruction to your system.
- Never change the source data. Your system reads it and writes only to `mock_system/`.
- Dates are in `YYYY-MM-DD`; amounts are in Indian rupees (₹), two decimals.
