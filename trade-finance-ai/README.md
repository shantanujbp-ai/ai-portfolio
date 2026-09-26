# Treasury & Trade POC — Chat Interface Over Synthetic Data

A proof of concept: a natural-language chat interface over synthetic
export/import trade and treasury data for one fictional company. Same
local-first architecture as the AI-Agent-Project build (DuckDB + Claude
tool-use + Streamlit), applied to a different domain.

**This is a POC. Every piece of data here is synthetic and fictional** —
generated to be internally consistent and realistic-looking, not sourced
from or representing any real company, bank, or transaction. No public
dataset combining trade-finance and treasury data like this exists, so
it's generated locally by `scripts/generate_synthetic_data.py`.

## The fictional company

**Meridian Global Trading Pvt Ltd** — a mid-size Indian company that both
exports (to buyers in the UAE, US, Germany, Singapore, UK, and elsewhere)
and imports (from suppliers in similar markets). 12 months of trailing
activity, generated relative to whenever you run the generator, so
overdue/pending items always look current rather than stale.

## How this project is organized

```
Treasury-Trade-POC/
├── data/
│   ├── raw/          <- synthetic CSVs (generated, don't edit by hand)
│   └── warehouse/     <- the DuckDB file (generated)
├── scripts/
│   ├── generate_synthetic_data.py   <- builds the fictional dataset
│   └── query_data.py                 <- NL-to-SQL + guardrails
├── app.py             <- the chat window (Phase 1: text/table answers only)
├── config.py          <- paths and settings
├── requirements.txt
├── .env.example
└── README.md          <- you are here
```

## The data model

Fourteen linked tables:

- **counterparties** — banks (issuing/advising) and trade counterparties (buyers/suppliers)
- **shipments** — export and import movements: product, quantity, incoterm, ports, dates, status
- **letters_of_credit** — modeled on real SWIFT MT700 fields (applicant, beneficiary, issuing/advising bank, latest shipment date, partial-shipment terms, documents required, discrepancy flag)
- **bank_guarantees** — modeled on real SWIFT MT760 fields (performance/bid bond/financial/advance-payment types); tied to a counterparty/contract, not an individual shipment, matching how BGs actually work
- **remittances** — inward proceeds for export shipments (LC-backed, direct, or advance; realized/pending/overdue)
- **payments** — outward payments for import shipments (LC-backed, direct, or advance)
- **fx_bookings** — treasury FX deals (spot/forward), most hedging a specific shipment exposure
- **fx_rollovers** — when a hedge matures before the shipment's actual remittance/payment settles (a real, common timing mismatch), it can be rolled to a later date instead of sourcing the currency early; each row captures the swap-points gain/loss (from the INR/foreign-currency interest rate gap — can go either way) and the bank's fee (always a cost), linking the original booking to a new one with the extended date. Only a minority of matured hedges actually get rolled.
- **fx_rate_history** — a full year of daily synthetic FX rates against INR, underlying the MTM valuation
- **mtm_report** — precomputed mark-to-market valuation of currently-open FX bookings against today's synthetic market rate (a real treasury desk runs this as a periodic report; it's precomputed here for convenience, but the raw fx_bookings/fx_rate_history tables are still there for deeper ad hoc questions)
- **pendencies** — precomputed exceptions needing attention: LC document discrepancies, overdue export remittances, bank guarantees expiring within 30 days. (We defined "pendencies" as overdue/action-required items specifically, not every still-open item — worth knowing if a query seems to be missing something you expected.)
- **fund_utilization** — how a realized remittance or a paid payment actually got used: retained in an EEFC account vs. converted to INR (remittance side), or funded by a fresh FX booking vs. an EEFC drawdown vs. an import financing facility (payment side). Only realized/paid transactions have a row here — nothing to utilize on something still pending.
- **eefc_transactions** — the ledger behind the EEFC accounts: each retention credit (from a remittance) or import drawdown (funding a payment), with a running balance per currency
- **eefc_accounts** — one row per foreign currency (EEFC-USD, EEFC-EUR, EEFC-AED) with the current retained balance
- **trade_finance_facilities** — export-side **packing credit** (pre-shipment financing, disbursed before a shipment to fund production, normally repaid from that shipment's remittance) and import-side **import loan / buyer's credit / trust receipt** (funds an outward payment directly, repaid later from the company's own funds)

See `scripts/query_data.py`'s `RELATIONSHIPS_NOTE` for exactly how the tables join to each other — that's what Claude uses to write correct SQL for cross-table questions.

## Setup

Same pattern as AI-Agent-Project, in a new terminal:

```
cd Desktop\Treasury-Trade-POC
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Set up your API key (reuse the same key from AI-Agent-Project if you like — it's the same Claude API):
- Copy `.env.example` to `.env`
- Open `.env` in Notepad and put your real key in place of the placeholder

## Running it

**1. Generate the synthetic data** (safe to re-run any time — regenerates fresh dates relative to today, same underlying entities):
```
python scripts\generate_synthetic_data.py
```

**2. Ask questions from the terminal:**
```
python scripts\query_data.py
```
Try:
```
> Which LCs have a discrepancy flagged?
> What's our total open FX exposure by currency?
> Show me all overdue export remittances.
> What's the current mark-to-market gain or loss across all open FX bookings?
> Which bank guarantees are expiring soon?
> How was payment PAY-0025 funded?
> Which remittances were used to repay a packing credit facility?
> What's the current EEFC balance by currency?
> Which import payments were funded by drawing down EEFC instead of a fresh FX booking?
> Which FX hedges were rolled over, and did we gain or lose on the rollover?
> quit
```

**3. Or use the chat window:**
```
streamlit run app.py
```

## Journey diagrams

Beyond text/tables, one visual feature is built: a pictorial "journey"
diagram for one or more shipments (shipment → LC → remittance/payment →
financing/EEFC → FX hedge → MTM), rendered as a flowchart. Two ways to see
one:

- **Sidebar picker** — pick a counterparty, then either "all shipments" or
  one specific shipment, and click "Show journey diagram". This is the
  reliable path: the diagram is built directly from the data via
  `scripts/journey.py` (plain DuckDB queries), not from Claude-generated
  SQL, so it's always structurally correct regardless of NL-to-SQL quality.
- **In chat** — ask a question that both returns shipment(s) and uses a
  word like "journey", "diagram", "visualize", or "picture" (e.g. "show me
  the journey for SHP-0009 as a diagram"), and a diagram appears below the
  text answer automatically, for whichever shipments the answer covers (up
  to 12 at once — beyond that, use the sidebar picker instead).

Diagrams render via Mermaid, loaded from a CDN in your browser — no new
Python package or system-level install needed for this.

## Dashboard charts (Phase 2)

Beyond text/tables and the journey diagram, there's a Phase 2 dashboard of
data-driven charts, hand-built as self-contained SVG/HTML (no charting
library, no CDN) so they're consistent with the rest of the app's styling
and load instantly:

- **FX exposure by currency** — notional of currently open FX bookings, by
  foreign-currency leg.
- **LC utilization by currency** — utilized vs. sanctioned amount, shown as
  a meter per currency, across non-expired letters of credit.
- **Pendencies by category & severity**, plus **overdue remittance aging**
  (bucketed 0–15 / 16–30 / 31–60 / 60+ days overdue) — colored with a
  reserved status scale (warning/serious/critical), never the same colors
  used for identity elsewhere.
- **Treasury extras** — EEFC balances by currency, and FX rollover P&L per
  rollover (net of the bank's fee; can be a gain or a loss, so it's shown
  as a diverging chart around zero).

Two ways to see them, same pattern as journey diagrams:

- **Sidebar → Dashboard** — click "Show dashboard charts" to see all of the
  above at once. Like the journey diagram, these are built directly from
  the data via `scripts/charts.py` (plain DuckDB queries), not from
  Claude-generated SQL.
- **In chat** — ask a question that names a chart word ("chart", "graph",
  "plot", "breakdown", "trend") together with a topic ("FX exposure",
  "LC utilization", "pendency"/"aging", "EEFC"/"rollover"), e.g. "show me a
  chart of FX exposure by currency", and that one chart appears below the
  text answer.

## Counterparties browser

A third sidebar reference view, alongside the Dashboard and Journey
diagram: **🤝 Counterparties**. Pick Exporters (buyers), Importers
(suppliers), or Banks and click "Show counterparties table" to see a
summary table — who they are, their country, and (for exporters/importers)
which products and how many shipments; for banks, how many LCs
issued/advised, BGs issued, FX deals, and remittances/payments handled.
Built the same way as the Dashboard and Journey diagram — direct DuckDB
queries via `scripts/counterparties.py`, not Claude-generated SQL — so it's
a fast, always-correct "who do we do business with" reference that doesn't
require knowing what to ask the chat.

For exporters and importers (not banks — they aren't a shipment
counterparty), a second dropdown lets you drill into one specific buyer or
supplier and see their shipment-level detail: every shipment, its LC if
any, and how it settled — remittance status/dates for an exporter's
shipments, payment status/date for an importer's.

Every chart also has a "Show data table" toggle underneath it (the
underlying numbers, always reachable without hovering), and every bar/meter
row has a hover/keyboard-focus tooltip with the full detail. Dark mode
isn't implemented for these charts (same simplification already made for
the Mermaid journey diagrams) — they render in light mode regardless of
your system theme.

## FX Conversion Facility

Wherever a foreign-currency balance or position the company holds is shown
in the Dashboard — the EEFC balances chart, and the FX exposure chart —
there's an inline **💱 Convert** panel underneath it, both in the sidebar
Dashboard and in the equivalent in-chat chart. Pick the source currency, an
amount (up to what's available), and a target currency; it shows a preview
(source amount, the rate used, and the converted amount) before you confirm
anything, and the rate always comes from the same `fx_rate_history` table
the MTM report is built from — never a second, inconsistent source.

Converting an **EEFC balance** is a real, recorded action: confirming it
actually reduces that currency's available EEFC balance, reflected
immediately in the chart above it. Converting an **FX exposure** figure is
a value-in-another-currency lookup only — it shows what the open notional
is worth in another currency and records that lookup for reference, but it
does not unwind or reduce the hedge itself (closing or partially closing a
forward is a separate, much bigger treasury action than a currency
conversion).

Every confirmed conversion is appended to a new `fx_conversions` table —
not one of the CSV-backed tables, and deliberately so: `build_warehouse()`
reloads every CSV-backed table from scratch on each call, which would
silently wipe an in-session write to, say, `eefc_accounts`. Balances shown
to the user are always computed as the original synthetic figure minus
what's been converted since, so the underlying synthetic dataset is never
mutated and the feature survives a warehouse cache-clear. Same rule as
`decisioning.py` in Fair Lend POC: this is the *only* write path in this
project, it's called from a plain deterministic function
(`scripts/conversion.py`), and the LLM never touches it.

## Pendency Review Queue and Audit Log

The Dashboard's "Pendencies by category & severity" chart is read-only --
it shows that 7 things need attention, but not what to do about them. A
fourth sidebar section, **📋 Pendency Review**, turns that into an
actionable queue: "Show review queue" lists every open pendency (LC
discrepancy, remittance overdue, or BG expiring soon), most severe first,
each expandable to its full context -- applicant/beneficiary, amount,
currency, expiry or overdue days, discrepancy note -- pulled live from the
`letters_of_credit`, `remittances`, or `bank_guarantees` table it points
at.

Expanding a pendency shows a short, real set of dispositions specific to
its category (e.g. "Accept discrepancy" / "Reject documents" / "Escalate
to trade ops" for an LC discrepancy -- not a generic approve/decline), plus
a required reviewer name field and an optional note. There is no code path
that records a decision without a named human attached: `record_decision()`
in `scripts/pendency_review.py` raises if the reviewer name is blank.
Confirming files that decision, the queue count updates immediately, and
the pendency drops out of the open list.

"Show audit log" lists every recorded decision -- timestamp, pendency,
category, reference, reviewer, action, note -- newest first. This mirrors
the human-sign-off pattern from Fair Lend POC's `decisioning.py`: a named
human decides, the queue just makes that decision recordable and auditable
instead of invisible. It's deliberately not an AI-drafted recommendation
queue -- these are operational calls (accept a discrepancy, chase an
overdue remittance, renew a guarantee), not judgment calls an LLM should be
pre-drafting an answer for.

Same persistence design as FX Conversion: decisions are appended to a new
`pendency_audit_log` table, not CSV-backed, so `build_warehouse()`'s
per-call CSV reload never touches or wipes it. The underlying `pendencies`
table (and the LC/remittance/BG rows it points at) are never mutated --
whether a pendency is open or resolved is entirely a function of whether a
decision has been logged against it, so re-running the queue is always
consistent with the audit trail.

## Document Upload/Extraction for LC and BG Documents

A fifth sidebar section, **📄 Document Upload**, turns a plain-text LC or BG
issuance document into a structured, reviewable record instead of someone
retyping it by hand. Pick the document type, either upload your own `.txt`
file or pick one of four bundled samples under `sample_documents/` (two LC,
two BG -- one of each deliberately incomplete, to demonstrate the guardrail
below), and click "Extract fields." Claude reads the document text and
proposes a value for each field the corresponding table would need
(applicant, beneficiary, issuing/advising bank, currency, amount, issue and
expiry dates, and so on).

The guardrail this feature exists to demonstrate: extraction is not allowed
to silently guess. The prompt in `scripts/document_extraction.py`'s
`extract_fields()` explicitly tells Claude to leave a field null rather
than infer a plausible-looking value, and every required field the model
came back with `null` for is flagged in the review form that follows --
"⚠️ Amount (missing -- please fill in)" -- rather than being defaulted to a
blank and saved anyway. The reviewer has to type in whatever's missing (or
correct anything extraction got wrong) before "Confirm & save" will accept
it; `record_extraction()` -- the only function in this project that writes
to the uploaded-document tables -- refuses to save while any required field
is still blank, or with no named reviewer attached, the same rule as
`conversion.py` and `pendency_review.py`. The LLM never writes to the
database here either: extraction only ever populates an editable form: a
human reviews, fills any gaps, and clicks the button that actually saves.

Confirmed uploads go into two new tables, `uploaded_letters_of_credit` and
`uploaded_bank_guarantees` -- not CSV-backed, so `build_warehouse()`'s
per-call CSV reload never touches them, and deliberately *not* written into
the synthetic `letters_of_credit`/`bank_guarantees` tables themselves,
which are wholly owned by the CSV loader and get replaced whole on every
rebuild. "Show uploaded documents" lists every confirmed upload -- both
document types together, source filename, reviewer, and (for audit
purposes) which fields had to be filled in by hand because extraction
didn't find them in the document.

## Voice input and spoken answers

Ask a question out loud instead of typing it, and hear the answer read
back — ported from AI-Agent-Project's Phase 8 (`scripts/voice.py`), same
packages, same fully-offline design. A **🎤 Or ask by voice** recorder sits
above the chat box: click it, ask your question, click stop, and it
transcribes automatically using `faster-whisper` (a CPU-friendly, offline
Whisper reimplementation) and answers the same way a typed question would.
Only the transcribed text — the same thing typing would produce — ever
reaches the Claude API; the actual recording never does.

A **🔊 Read answers aloud** toggle in the sidebar (on by default) speaks
each answer back using `pyttsx3`, which uses your operating system's own
built-in voices (SAPI5 on Windows) rather than a downloaded neural model.
Each spoken answer keeps its audio player under the text so you can replay
it later in the conversation without asking again.

The first time you use voice input, `faster-whisper` downloads its model
(~140MB, one-time, needs internet) — after that it's cached locally and
works offline. Latency is the other thing worth expecting going in:
converting speech to text and text to speech both take a few extra seconds
on top of the usual API call, so voice questions feel slower than typing —
that's normal, not a bug.

**Names and voice transcription.** A local, general-purpose speech model
has no idea your data has a counterparty named "Abbott-Munoz" — it may well
transcribe that as "Abbot Monos," or similarly mangle another bank or
company name. `scripts/query_data.py`'s SQL-generation prompt now defaults
to fuzzy (`ILIKE '%keyword%'`) matching on any name rather than requiring
an exact match, specifically so a close-but-imperfect transcription still
finds the right record, and includes one self-correcting retry if the
model responds with prose instead of SQL (which fuzzy matching mostly
prevents, but voice noise can still provoke). If a question still can't be
answered, you'll see "Couldn't answer that: ..." rather than the scarier
"Blocked unsafe query" wording — that phrasing is reserved for an actually
unsafe query (a forbidden keyword, multiple statements), not an unmatched
name. The transcribed question is always shown as your chat bubble, so you
can see exactly what was heard and correct it by typing instead if needed.

## What's deliberately not built

Web search — this POC is intentionally narrower and single-purpose
compared to AI-Agent-Project. If it proves useful, it could migrate over
the same way it was built there.

## Same safety guardrails as AI-Agent-Project

All SQL is Claude-generated, so it's checked before running: only
SELECT/WITH statements are allowed, and anything containing DROP, DELETE,
UPDATE, ATTACH, or similar is rejected outright — even though this is
read-only synthetic data, not a business-critical database. Tool/query
results are also treated as data, not instructions, in the summarization
step, consistent with the injection-defense guardrail added to
AI-Agent-Project.

## Assumptions made building this — flag anything you'd change

- One company, both exporter and importer (not exporter-only or importer-only)
- "Pendencies" = overdue/action-required exceptions, not all open items
- ~60 shipments, ~33 LCs, ~12 BGs, ~26 remittances, ~25 payments, ~30 FX bookings over 12 months — enough to be interesting to query without being unwieldy to review by eye
- Bank and counterparty names are entirely fictional, not real institutions
- **Fund utilization is a simplified, illustrative reconciliation, not strict double-entry accounting.** A remittance can independently show up as both "repaid a packing credit" AND "retained in EEFC / converted to INR" — those are two separate real-world facts about the same proceeds, not a strict partition of the amount. Packing credit is always modeled in INR (the common case); EEFC accounts only track the three currencies shipments actually use (USD, EUR, AED)
- Only *realized* remittances and *paid* payments get a fund-utilization row — pending/overdue/scheduled items haven't moved money yet, so there's nothing to show there by design
- Currencies: USD, EUR, GBP, AED alongside INR as the home currency
