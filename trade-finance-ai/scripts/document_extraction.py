"""
Treasury & Trade POC -- Document upload/extraction for LC and BG documents.

Lets a user drop in a plain-text LC or BG issuance advice and have Claude
pull out the structured fields that would otherwise be typed in by hand --
applicant, beneficiary, amount, expiry date, and so on, matching the same
columns as the synthetic `letters_of_credit` / `bank_guarantees` tables.

The guardrail this feature exists to demonstrate: extraction is not allowed
to silently guess. Claude is instructed to leave a field null if it isn't
actually stated in the document, and the required-field list below is
checked against that output -- anything missing is flagged to the human
reviewer rather than defaulted to a blank or a guess. Nothing is written to
the database from the extraction step itself; the extracted (and possibly
incomplete) fields only ever populate an editable review form. A named
human must fill in anything missing and click "Confirm & save" -- the same
"LLM never writes to the database" rule as conversion.py and
pendency_review.py: extraction proposes, `record_extraction()` (a plain,
deterministic function called only from that button) is the only thing
that ever writes a row.

Persistence note, same as the other two features: confirmed uploads go into
`uploaded_letters_of_credit` / `uploaded_bank_guarantees`, which are NOT
CSV-backed, so build_warehouse()'s per-call CSV reload never touches them.
They deliberately are NOT written into the synthetic `letters_of_credit` /
`bank_guarantees` tables themselves -- those two are wholly owned by the
CSV loader and get replaced whole on every rebuild, so anything appended to
them in-session would vanish on the next cache-clear. Keeping confirmed
uploads in their own tables (with the same columns, so they read the same
way) means a rebuild can never silently lose an uploaded document.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime

import anthropic
import duckdb

MODEL = "claude-haiku-4-5-20251001"

DOC_TYPES = ("LC", "BG")

# Fields Claude is asked to extract, per document type, and which of those
# are required -- i.e. which ones trigger the "flag as missing" guardrail
# rather than being nice-to-have context.
FIELD_SPECS = {
    "LC": {
        "all": [
            "direction", "applicant", "beneficiary", "issuing_bank", "advising_bank",
            "currency", "amount", "issue_date", "latest_shipment_date", "expiry_date",
            "partial_shipment_allowed", "documents_required",
        ],
        "required": [
            "applicant", "beneficiary", "issuing_bank", "currency", "amount",
            "issue_date", "expiry_date",
        ],
    },
    "BG": {
        "all": [
            "type", "applicant", "beneficiary", "issuing_bank", "currency",
            "amount", "issue_date", "expiry_date",
        ],
        "required": [
            "type", "applicant", "beneficiary", "issuing_bank", "currency",
            "amount", "issue_date", "expiry_date",
        ],
    },
}

FIELD_LABELS = {
    "direction": "Direction (import/export)",
    "applicant": "Applicant",
    "beneficiary": "Beneficiary",
    "issuing_bank": "Issuing bank",
    "advising_bank": "Advising bank",
    "currency": "Currency",
    "amount": "Amount",
    "issue_date": "Issue date",
    "latest_shipment_date": "Latest shipment date",
    "expiry_date": "Expiry date",
    "partial_shipment_allowed": "Partial shipment allowed",
    "documents_required": "Documents required",
    "type": "Guarantee type",
}


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS uploaded_letters_of_credit (
            lc_id VARCHAR,
            shipment_id VARCHAR,
            direction VARCHAR,
            applicant VARCHAR,
            beneficiary VARCHAR,
            issuing_bank VARCHAR,
            advising_bank VARCHAR,
            currency VARCHAR,
            amount DOUBLE,
            issue_date DATE,
            latest_shipment_date DATE,
            expiry_date DATE,
            partial_shipment_allowed BOOLEAN,
            documents_required VARCHAR,
            source_filename VARCHAR,
            uploaded_at TIMESTAMP,
            reviewer_name VARCHAR,
            fields_flagged_missing VARCHAR
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS uploaded_bank_guarantees (
            bg_id VARCHAR,
            type VARCHAR,
            applicant VARCHAR,
            beneficiary VARCHAR,
            issuing_bank VARCHAR,
            currency VARCHAR,
            amount DOUBLE,
            issue_date DATE,
            expiry_date DATE,
            source_filename VARCHAR,
            uploaded_at TIMESTAMP,
            reviewer_name VARCHAR,
            fields_flagged_missing VARCHAR
        )
    """)


def _strip_code_fence(text: str) -> str:
    return re.sub(r"^```(json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE).strip()


def extract_fields(client: anthropic.Anthropic, doc_type: str, raw_text: str) -> dict:
    """Ask Claude to pull the FIELD_SPECS[doc_type]['all'] fields out of raw_text.
    Returns a dict with exactly those keys -- any field not actually present in
    the document comes back as None (Claude is explicitly told not to guess).
    This function only ever returns proposed values; nothing here writes to
    the database."""
    if doc_type not in FIELD_SPECS:
        raise ValueError(f"Unknown document type {doc_type!r}.")
    fields = FIELD_SPECS[doc_type]["all"]
    system_prompt = (
        f"You extract structured fields from a {('letter of credit' if doc_type == 'LC' else 'bank guarantee')} "
        "issuance document. Return ONLY a single JSON object (no markdown fence, no commentary) "
        f"with exactly these keys: {fields}.\n\n"
        "Rules:\n"
        "- If a field is genuinely stated in the document, extract its value.\n"
        "- If a field is NOT stated, ambiguous, marked as pending/TBC, or you are not "
        "confident, set it to null. Do NOT guess, infer from typical values, or invent a "
        "plausible-looking number or date -- a null is far better than a wrong value here.\n"
        "- Dates must be plain ISO format YYYY-MM-DD.\n"
        "- amount must be a plain number (no currency symbol, no thousands separators).\n"
        "- partial_shipment_allowed and any other boolean-like field must be true, false, or null.\n"
        "- direction must be exactly 'import', 'export', or null.\n"
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        system=system_prompt,
        messages=[{"role": "user", "content": raw_text}],
    )
    raw = _strip_code_fence(response.content[0].text)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Extraction did not return valid JSON: {raw[:300]!r}") from exc
    # Guarantee every expected key is present even if the model dropped one.
    return {f: parsed.get(f) for f in fields}


def coerce_form_fields(doc_type: str, raw: dict) -> tuple[dict, list[str]]:
    """Turn the plain strings collected from the review form (every field is a
    text input, extracted or human-typed) into the types the table columns
    expect -- float for amount, bool for partial_shipment_allowed. Returns
    (coerced, errors): errors lists any field whose text couldn't be parsed,
    kept separate from missing_required_fields (which only checks blankness)
    so the two failure modes -- "still empty" vs. "filled in but not valid" --
    get their own messages instead of one being silently swallowed as null."""
    if doc_type not in FIELD_SPECS:
        raise ValueError(f"Unknown document type {doc_type!r}.")
    coerced: dict = {}
    errors: list[str] = []
    for f in FIELD_SPECS[doc_type]["all"]:
        v = raw.get(f)
        v = v.strip() if isinstance(v, str) else v
        if not v:
            coerced[f] = None
            continue
        if f == "amount":
            try:
                coerced[f] = float(v.replace(",", ""))
            except ValueError:
                errors.append(f"{FIELD_LABELS.get(f, f)}: {v!r} is not a valid number.")
                coerced[f] = None
        elif f == "partial_shipment_allowed":
            low = v.lower()
            if low in ("true", "yes", "y", "allowed", "1"):
                coerced[f] = True
            elif low in ("false", "no", "n", "not allowed", "0"):
                coerced[f] = False
            else:
                errors.append(f"{FIELD_LABELS.get(f, f)}: {v!r} -- expected true/false.")
                coerced[f] = None
        else:
            coerced[f] = v
    return coerced, errors


def missing_required_fields(doc_type: str, extracted: dict) -> list[str]:
    required = FIELD_SPECS[doc_type]["required"]

    def _blank(v):
        return v is None or (isinstance(v, str) and not v.strip())

    return [f for f in required if _blank(extracted.get(f))]


def record_extraction(
    con: duckdb.DuckDBPyConnection, *, doc_type: str, fields: dict,
    source_filename: str, reviewer_name: str, fields_flagged_missing: list[str],
) -> str:
    """The ONLY function in this project that writes to uploaded_letters_of_credit
    or uploaded_bank_guarantees. Requires a non-empty reviewer name and that every
    required field for doc_type is actually filled in (by extraction or by the
    reviewer correcting the form) -- same rule as pendency_review.record_decision:
    no code path records a document with a required field silently blank."""
    if doc_type not in FIELD_SPECS:
        raise ValueError(f"Unknown document type {doc_type!r}.")
    if not reviewer_name or not reviewer_name.strip():
        raise ValueError("A named human reviewer is required to save an uploaded document.")
    still_missing = missing_required_fields(doc_type, fields)
    if still_missing:
        raise ValueError(
            "Cannot save -- required fields are still blank: "
            + ", ".join(FIELD_LABELS.get(f, f) for f in still_missing)
        )
    ensure_schema(con)

    if doc_type == "LC":
        lc_id = f"LC-UP-{uuid.uuid4().hex[:6].upper()}"
        con.execute(
            """INSERT INTO uploaded_letters_of_credit VALUES
               (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                lc_id, fields.get("direction"), fields.get("applicant"), fields.get("beneficiary"),
                fields.get("issuing_bank"), fields.get("advising_bank"), fields.get("currency"),
                fields.get("amount"), fields.get("issue_date"), fields.get("latest_shipment_date"),
                fields.get("expiry_date"), fields.get("partial_shipment_allowed"),
                fields.get("documents_required"), source_filename, datetime.now(),
                reviewer_name.strip(), ", ".join(fields_flagged_missing) or None,
            ],
        )
        return lc_id

    bg_id = f"BG-UP-{uuid.uuid4().hex[:6].upper()}"
    con.execute(
        """INSERT INTO uploaded_bank_guarantees VALUES
           (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            bg_id, fields.get("type"), fields.get("applicant"), fields.get("beneficiary"),
            fields.get("issuing_bank"), fields.get("currency"), fields.get("amount"),
            fields.get("issue_date"), fields.get("expiry_date"), source_filename,
            datetime.now(), reviewer_name.strip(), ", ".join(fields_flagged_missing) or None,
        ],
    )
    return bg_id


def list_uploads(con: duckdb.DuckDBPyConnection, doc_type: str | None = None):
    ensure_schema(con)
    if doc_type == "LC":
        return con.execute(
            "SELECT * FROM uploaded_letters_of_credit ORDER BY uploaded_at DESC"
        ).fetchdf()
    if doc_type == "BG":
        return con.execute(
            "SELECT * FROM uploaded_bank_guarantees ORDER BY uploaded_at DESC"
        ).fetchdf()
    lc_df = con.execute(
        "SELECT lc_id AS doc_id, 'LC' AS doc_type, applicant, beneficiary, currency, "
        "amount, issue_date, expiry_date, source_filename, uploaded_at, reviewer_name, "
        "fields_flagged_missing FROM uploaded_letters_of_credit"
    ).fetchdf()
    bg_df = con.execute(
        "SELECT bg_id AS doc_id, 'BG' AS doc_type, applicant, beneficiary, currency, "
        "amount, issue_date, expiry_date, source_filename, uploaded_at, reviewer_name, "
        "fields_flagged_missing FROM uploaded_bank_guarantees"
    ).fetchdf()
    import pandas as pd
    combined = pd.concat([lc_df, bg_df], ignore_index=True)
    return combined.sort_values("uploaded_at", ascending=False) if not combined.empty else combined
