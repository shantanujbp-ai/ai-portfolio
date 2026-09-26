"""
Treasury & Trade POC -- Pendency Review Queue + Audit Log.

Turns the read-only "Pendencies by category & severity" dashboard chart
(scripts/charts.py) into an actionable queue, mirroring the accountability
pattern already proven out in Fair Lend POC's scripts/decisioning.py:
nothing is ever recorded without a named human reviewer attached, and every
recorded decision is logged append-only, with the actor and timestamp,
alongside the pendency it resolved.

Deliberately narrow scope: this is a human-only decision queue, not an
AI-drafted recommendation queue like Fair Lend's credit decisioning --
these are operational treasury/trade actions (accept a discrepancy, renew
a guarantee, chase an overdue remittance), not judgment calls an LLM should
be pre-drafting an answer for. A named human decides; the queue just makes
that decision recordable and auditable instead of invisible.

Same persistence note as conversion.py: pendency_audit_log is NOT one of
the CSV-backed tables build_warehouse() reloads on every call, so a
recorded decision survives a warehouse cache-clear that would otherwise
silently wipe an in-session write. The underlying `pendencies` table (and
the LC/remittance/BG rows it points at) are never mutated -- resolved vs.
open is entirely a function of whether a decision has been logged.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import duckdb

CATEGORY_LABELS = {
    "lc_discrepancy": "LC discrepancy",
    "remittance_overdue": "Remittance overdue",
    "bg_expiring": "BG expiring soon",
}

# The set of dispositions a reviewer can record, per pendency category --
# deliberately real treasury/trade actions, not a generic approve/decline.
ACTIONS = {
    "lc_discrepancy": ["Accept discrepancy", "Reject documents", "Escalate to trade ops"],
    "remittance_overdue": ["Follow up with buyer", "Escalate to collections", "Write off"],
    "bg_expiring": ["Renew guarantee", "Let lapse", "Escalate to relationship manager"],
}

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS pendency_audit_log (
            log_id VARCHAR,
            pendency_id VARCHAR,
            category VARCHAR,
            reference_id VARCHAR,
            event_type VARCHAR,       -- 'pendency_flagged' | 'human_decision'
            actor VARCHAR,
            action_taken VARCHAR,
            note VARCHAR,
            event_timestamp TIMESTAMP
        )
    """)


def _context(con: duckdb.DuckDBPyConnection, category: str, reference_id: str) -> dict:
    if category == "lc_discrepancy":
        df = con.execute("""
            SELECT lc_id AS id, applicant, beneficiary, currency, amount, utilized_amount,
                   expiry_date, discrepancy_note
            FROM letters_of_credit WHERE lc_id = ?
        """, [reference_id]).fetchdf()
    elif category == "remittance_overdue":
        df = con.execute("""
            SELECT remittance_id AS id, shipment_id, currency, amount, expected_date, days_overdue
            FROM remittances WHERE remittance_id = ?
        """, [reference_id]).fetchdf()
    elif category == "bg_expiring":
        df = con.execute("""
            SELECT bg_id AS id, applicant, beneficiary, currency, amount, expiry_date
            FROM bank_guarantees WHERE bg_id = ?
        """, [reference_id]).fetchdf()
    else:
        return {}
    return df.to_dict("records")[0] if not df.empty else {}


def open_queue(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Pendencies with no recorded human decision yet, most severe first."""
    ensure_schema(con)
    df = con.execute("""
        SELECT p.pendency_id, p.category, p.severity, p.reference_id, p.description
        FROM pendencies p
        WHERE NOT EXISTS (
            SELECT 1 FROM pendency_audit_log a
            WHERE a.pendency_id = p.pendency_id AND a.event_type = 'human_decision'
        )
    """).fetchdf()
    rows = df.to_dict("records")
    rows.sort(key=lambda r: (SEVERITY_ORDER.get(r["severity"], 9), r["category"]))
    for r in rows:
        r["context"] = _context(con, r["category"], r["reference_id"])
    return rows


def record_decision(
    con: duckdb.DuckDBPyConnection, *, pendency_id: str, category: str, reference_id: str,
    reviewer_name: str, action_taken: str, note: str = "",
) -> str:
    """The ONLY function in this project that writes to pendency_audit_log.
    Requires a non-empty reviewer name -- there is deliberately no code path
    that records a pendency decision with no named human accountable for
    it, same rule as Fair Lend POC's record_human_decision."""
    if not reviewer_name or not reviewer_name.strip():
        raise ValueError("A named human reviewer is required to record a decision.")
    valid_actions = ACTIONS.get(category, [])
    if action_taken not in valid_actions:
        raise ValueError(f"Invalid action {action_taken!r} for category {category!r}.")
    ensure_schema(con)

    log_id = f"LOG-{uuid.uuid4().hex[:8].upper()}"
    con.execute(
        """INSERT INTO pendency_audit_log VALUES (?, ?, ?, ?, 'human_decision', ?, ?, ?, ?)""",
        [log_id, pendency_id, category, reference_id, reviewer_name.strip(),
         action_taken, note.strip(), datetime.now()],
    )
    return log_id


def audit_trail(con: duckdb.DuckDBPyConnection, limit: int = 200):
    ensure_schema(con)
    return con.execute("""
        SELECT event_timestamp, pendency_id, category, reference_id, actor, action_taken, note
        FROM pendency_audit_log ORDER BY event_timestamp DESC LIMIT ?
    """, [limit]).fetchdf()


def summary_counts(con: duckdb.DuckDBPyConnection) -> dict:
    ensure_schema(con)
    total = con.execute("SELECT COUNT(*) FROM pendencies").fetchone()[0]
    decided = con.execute(
        "SELECT COUNT(DISTINCT pendency_id) FROM pendency_audit_log WHERE event_type = 'human_decision'"
    ).fetchone()[0]
    return {"total": total, "decided": decided, "open": max(0, total - decided)}
