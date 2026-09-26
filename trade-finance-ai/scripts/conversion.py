"""
Treasury & Trade POC -- FX Conversion Facility (FR-6, Treasury-Trade-POC-HLRD
Section 5). Requested addition: "We also want to include FX conversion
facility in each product for the end user. Example: if I have USD 20k in
EEFC shown in the dashboard, I should also be able to convert FX from USD
to INR."

Same discipline as the rest of this project: direct, hand-written DuckDB
queries (never Claude-generated SQL), and -- same rule as decisioning-style
writes elsewhere in these two POCs -- the LLM never touches this write path.
A conversion is only ever recorded by a plain, deterministic function called
from a human clicking "Confirm conversion" in the UI.

Persistence note: build_warehouse() in query_data.py does `CREATE OR REPLACE
TABLE` for every CSV in data/raw on each call, which would silently wipe any
in-session write to a CSV-backed table (eefc_accounts, fx_bookings, ...) the
next time the warehouse cache is cleared. So conversions are never written
into those tables directly. Instead they land in a new, non-CSV-backed
ledger table (fx_conversions) that build_warehouse()'s CSV loop never
touches, and every balance shown to the user is computed as
"base CSV figure minus what's been converted since" -- the original
synthetic data stays untouched and the feature is safe across cache clears.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import duckdb

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS fx_conversions (
            conversion_id VARCHAR,
            source_type VARCHAR,       -- 'eefc_balance' | 'fx_exposure'
            source_ref VARCHAR,        -- EEFC-USD, or the exposure currency for fx_exposure
            source_currency VARCHAR,
            source_amount DOUBLE,
            target_currency VARCHAR,
            rate DOUBLE,
            target_amount DOUBLE,
            converted_at TIMESTAMP,
            note VARCHAR
        )
    """)


# ---------------------------------------------------------------------------
# Rates -- always sourced from fx_rate_history, the same table the MTM
# report is built from (FR-6.4), never a second, independent source.
# ---------------------------------------------------------------------------
def supported_currencies(con: duckdb.DuckDBPyConnection) -> list[str]:
    rows = con.execute(
        "SELECT DISTINCT split_part(currency_pair, '/', 1) AS ccy FROM fx_rate_history ORDER BY 1"
    ).fetchall()
    return ["INR"] + [r[0] for r in rows]


def _latest_rate_to_inr(con: duckdb.DuckDBPyConnection, currency: str) -> float:
    if currency == "INR":
        return 1.0
    row = con.execute(
        "SELECT rate FROM fx_rate_history WHERE currency_pair = ? ORDER BY date DESC LIMIT 1",
        [f"{currency}/INR"],
    ).fetchone()
    if not row:
        raise ValueError(f"No FX rate available for {currency}/INR in fx_rate_history.")
    return float(row[0])


def get_rate(con: duckdb.DuckDBPyConnection, from_currency: str, to_currency: str) -> float:
    """1 unit of from_currency, expressed in to_currency -- via each side's
    latest published rate against INR (cross-rate), so a USD->AED quote is
    exactly as consistent with the rest of the app as a USD->INR one."""
    if from_currency == to_currency:
        return 1.0
    from_inr = _latest_rate_to_inr(con, from_currency)
    to_inr = _latest_rate_to_inr(con, to_currency)
    return from_inr / to_inr


# ---------------------------------------------------------------------------
# Balances, net of conversions already recorded
# ---------------------------------------------------------------------------
def eefc_balances(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Current EEFC balance per currency, net of any conversions already
    confirmed this session. The underlying eefc_accounts figure (from the
    synthetic CSV) is never mutated -- this is base minus converted-away."""
    ensure_schema(con)
    df = con.execute("""
        SELECT a.currency, a.current_balance AS base_balance,
               COALESCE((SELECT SUM(source_amount) FROM fx_conversions c
                         WHERE c.source_type = 'eefc_balance' AND c.source_currency = a.currency), 0) AS converted
        FROM eefc_accounts a ORDER BY a.current_balance DESC
    """).fetchdf()
    out = []
    for r in df.itertuples():
        available = max(0.0, r.base_balance - r.converted)
        out.append({"currency": r.currency, "available": available, "base": r.base_balance})
    return out


def fx_exposure_positions(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Open FX exposure notional per currency -- the conversion here is a
    value-in-another-currency lookup (FR-6.1/6.5), not an unwind of the
    hedge, so the notional itself is never reduced by a confirmed
    conversion; only the base figure is shown."""
    df = con.execute("""
        SELECT CASE WHEN sell_currency = 'INR' THEN buy_currency ELSE sell_currency END AS currency,
               SUM(amount) AS exposure
        FROM fx_bookings WHERE status = 'open'
        GROUP BY 1 ORDER BY 2 DESC
    """).fetchdf()
    return [{"currency": r.currency, "available": r.exposure} for r in df.itertuples()]


# ---------------------------------------------------------------------------
# The write path -- the only place fx_conversions is ever inserted into.
# ---------------------------------------------------------------------------
def record_conversion(
    con: duckdb.DuckDBPyConnection, *, source_type: str, source_ref: str,
    source_currency: str, source_amount: float, target_currency: str, note: str = "",
) -> dict:
    if source_amount is None or source_amount <= 0:
        raise ValueError("Conversion amount must be greater than zero.")
    if source_currency == target_currency:
        raise ValueError("Source and target currency must be different.")
    ensure_schema(con)

    rate = get_rate(con, source_currency, target_currency)
    target_amount = source_amount * rate
    conversion_id = f"CNV-{uuid.uuid4().hex[:8].upper()}"
    now = datetime.now()

    con.execute(
        """INSERT INTO fx_conversions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [conversion_id, source_type, source_ref, source_currency, source_amount,
         target_currency, rate, target_amount, now, note],
    )
    return {
        "conversion_id": conversion_id, "rate": rate, "target_amount": target_amount,
        "converted_at": now,
    }


def recent_conversions(con: duckdb.DuckDBPyConnection, limit: int = 20):
    ensure_schema(con)
    return con.execute(
        """SELECT converted_at, source_type, source_ref, source_currency, source_amount,
                  target_currency, rate, target_amount
           FROM fx_conversions ORDER BY converted_at DESC LIMIT ?""",
        [limit],
    ).fetchdf()
