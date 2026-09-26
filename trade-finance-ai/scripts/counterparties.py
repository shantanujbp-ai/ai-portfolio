"""
Treasury & Trade POC -- a "who do we do business with" reference browser:
exporters (buyers), importers (suppliers), and banks, each summarized from
the data directly.

Same philosophy as journey.py and charts.py: built from plain DuckDB
queries, never from Claude-generated SQL, so it's always fast and always
correct -- a browsing/reference view shouldn't depend on the NL-to-SQL
pipeline at all.
"""

import duckdb
import pandas as pd

VIEWS = ("exporters", "importers", "banks")

VIEW_LABELS = {
    "exporters": "Exporters (buyers)",
    "importers": "Importers (suppliers)",
    "banks": "Banks",
}


def _trade_counterparty_summary(con: duckdb.DuckDBPyConnection, direction: str, role_col: str) -> pd.DataFrame:
    df = con.execute(
        f"""
        SELECT c.name AS "{role_col}", c.country AS "Country",
               string_agg(DISTINCT s.product, ', ' ORDER BY s.product) AS "Products",
               COUNT(*) AS "Shipments"
        FROM shipments s
        JOIN counterparties c ON c.counterparty_id = s.counterparty_id
        WHERE s.direction = ?
        GROUP BY 1, 2
        ORDER BY "Shipments" DESC, "{role_col}"
        """,
        [direction],
    ).fetchdf()
    return df


def exporters_summary(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Export-side trade counterparties -- Meridian's buyers."""
    return _trade_counterparty_summary(con, "export", "Buyer")


def importers_summary(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Import-side trade counterparties -- Meridian's suppliers."""
    return _trade_counterparty_summary(con, "import", "Supplier")


def banks_summary(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Banks -- a different kind of counterparty, playing several roles
    (LC issuing/advising, BG issuing, FX counterparty, remittance/payment
    handling bank) rather than buying or selling goods. Counted separately
    per role and merged, since no single table captures all of them."""
    banks = con.execute(
        "SELECT counterparty_id, name AS \"Bank\", country AS \"Country\" "
        "FROM counterparties WHERE type = 'bank' ORDER BY name"
    ).fetchdf()

    role_queries = {
        "LCs issued": "SELECT issuing_bank AS counterparty_id, COUNT(*) AS n FROM letters_of_credit GROUP BY 1",
        "LCs advised": "SELECT advising_bank AS counterparty_id, COUNT(*) AS n FROM letters_of_credit GROUP BY 1",
        "BGs issued": "SELECT issuing_bank AS counterparty_id, COUNT(*) AS n FROM bank_guarantees GROUP BY 1",
        "FX deals": "SELECT counterparty_bank AS counterparty_id, COUNT(*) AS n FROM fx_bookings GROUP BY 1",
        "Remittances handled": "SELECT counterparty_bank AS counterparty_id, COUNT(*) AS n FROM remittances GROUP BY 1",
        "Payments handled": "SELECT counterparty_bank AS counterparty_id, COUNT(*) AS n FROM payments GROUP BY 1",
    }
    for col, sql in role_queries.items():
        counts = con.execute(sql).fetchdf()
        banks = banks.merge(counts, on="counterparty_id", how="left").rename(columns={"n": col})
        banks[col] = banks[col].fillna(0).astype(int)

    return banks.drop(columns=["counterparty_id"])


_SUMMARY_FUNCS = {
    "exporters": exporters_summary,
    "importers": importers_summary,
    "banks": banks_summary,
}


def get_summary(con: duckdb.DuckDBPyConnection, view: str) -> pd.DataFrame:
    if view not in _SUMMARY_FUNCS:
        return pd.DataFrame()
    return _SUMMARY_FUNCS[view](con)


def counterparty_names(con: duckdb.DuckDBPyConnection, view: str) -> list:
    """Names for the drill-down picker -- exporters/importers only (banks
    aren't shipment counterparties, so there's no per-shipment detail to
    show for them)."""
    if view not in ("exporters", "importers"):
        return []
    direction = "export" if view == "exporters" else "import"
    df = con.execute(
        """
        SELECT DISTINCT c.name FROM shipments s
        JOIN counterparties c ON c.counterparty_id = s.counterparty_id
        WHERE s.direction = ? ORDER BY 1
        """,
        [direction],
    ).fetchdf()
    return df["name"].tolist()


def _fmt_dates(df: pd.DataFrame, cols: list) -> pd.DataFrame:
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c]).dt.strftime("%Y-%m-%d")
    return df


def export_shipment_details(con: duckdb.DuckDBPyConnection, buyer_name: str) -> pd.DataFrame:
    """Per-shipment detail for one export buyer: the shipment itself, its
    LC if any, and how the proceeds settled (remittance) if realized yet."""
    df = con.execute(
        """
        SELECT s.shipment_id AS "Shipment", s.product AS "Product",
               s.quantity_mt AS "Qty (MT)", s.incoterm AS "Incoterm",
               s.origin_port AS "Origin", s.destination_port AS "Destination",
               s.etd AS "ETD", s.eta AS "ETA", s.status AS "Shipment status",
               s.invoice_value AS "Invoice value", s.currency AS "Currency",
               lc.lc_id AS "LC", lc.status AS "LC status",
               r.remittance_id AS "Remittance", r.status AS "Remittance status",
               r.expected_date AS "Remittance expected", r.actual_date AS "Remittance received",
               r.days_overdue AS "Days overdue"
        FROM shipments s
        JOIN counterparties c ON c.counterparty_id = s.counterparty_id
        LEFT JOIN letters_of_credit lc ON lc.shipment_id = s.shipment_id
        LEFT JOIN remittances r ON r.shipment_id = s.shipment_id
        WHERE s.direction = 'export' AND c.name = ?
        ORDER BY s.etd
        """,
        [buyer_name],
    ).fetchdf()
    return _fmt_dates(df, ["ETD", "ETA", "Remittance expected", "Remittance received"])


def import_shipment_details(con: duckdb.DuckDBPyConnection, supplier_name: str) -> pd.DataFrame:
    """Per-shipment detail for one import supplier: the shipment itself,
    its LC if any, and how it was paid (payment) if paid yet."""
    df = con.execute(
        """
        SELECT s.shipment_id AS "Shipment", s.product AS "Product",
               s.quantity_mt AS "Qty (MT)", s.incoterm AS "Incoterm",
               s.origin_port AS "Origin", s.destination_port AS "Destination",
               s.etd AS "ETD", s.eta AS "ETA", s.status AS "Shipment status",
               s.invoice_value AS "Invoice value", s.currency AS "Currency",
               lc.lc_id AS "LC", lc.status AS "LC status",
               p.payment_id AS "Payment", p.status AS "Payment status",
               p.payment_date AS "Payment date"
        FROM shipments s
        JOIN counterparties c ON c.counterparty_id = s.counterparty_id
        LEFT JOIN letters_of_credit lc ON lc.shipment_id = s.shipment_id
        LEFT JOIN payments p ON p.shipment_id = s.shipment_id
        WHERE s.direction = 'import' AND c.name = ?
        ORDER BY s.etd
        """,
        [supplier_name],
    ).fetchdf()
    return _fmt_dates(df, ["ETD", "ETA", "Payment date"])


def get_shipment_details(con: duckdb.DuckDBPyConnection, view: str, name: str) -> pd.DataFrame:
    if view == "exporters":
        return export_shipment_details(con, name)
    if view == "importers":
        return import_shipment_details(con, name)
    return pd.DataFrame()
