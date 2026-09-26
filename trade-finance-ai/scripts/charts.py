"""
Treasury & Trade POC -- Phase 2 dashboard charts: FX exposure by currency,
LC utilization, pendency aging/severity, and "treasury extras" (EEFC
balances by currency + FX rollover P&L).

Same philosophy as journey.py: charts are built from direct, hand-written
DuckDB queries -- never from Claude-generated SQL -- so they're always
structurally correct regardless of NL-to-SQL quality, and they render as
self-contained SVG/HTML (no charting library, no CDN, no new Python
package), following the project's data-viz skill: form chosen before
color, color assigned by job (sequential magnitude / status severity /
diverging polarity), hover tooltips, a legend wherever more than one color
is in play, and a "show data table" fallback for accessibility.

Color roles used here (see the skill's reference palette):
  - SEQ_BLUE / SEQ_BLUE_TRACK -- sequential magnitude, single hue
    (FX exposure, EEFC balances, LC utilization meters)
  - STATUS[...] -- reserved status scale, never reused as a series color
    (pendency severity + aging buckets)
  - DIVERGE_POS / DIVERGE_NEG / DIVERGE_NEUTRAL -- polarity around zero
    (FX rollover P&L, which can be a gain or a loss)
"""

from __future__ import annotations

import duckdb
import pandas as pd

import conversion

# ---------------------------------------------------------------------------
# Chat trigger detection -- mirrors journey.question_wants_diagram: keyword
# gated, and only fires when the question also names one of the four chart
# topics, so a chat-triggered chart is never a guess about what to plot.
# ---------------------------------------------------------------------------
CHART_INTENT_KEYWORDS = (
    "chart", "graph", "plot", "bar chart", "trend", "breakdown", "dashboard",
)

CHART_GROUPS = ("fx_exposure", "lc_utilization", "pendency", "treasury_extras")

CHART_GROUP_LABELS = {
    "fx_exposure": "FX exposure by currency",
    "lc_utilization": "LC utilization",
    "pendency": "Pendency aging & severity",
    "treasury_extras": "Treasury extras (EEFC balances + FX rollover P&L)",
}


def question_wants_chart(question: str) -> str | None:
    q = question.lower()
    if not any(kw in q for kw in CHART_INTENT_KEYWORDS):
        return None
    if "fx" in q and ("exposure" in q or "currency" in q):
        return "fx_exposure"
    if "lc" in q and "utili" in q:
        return "lc_utilization"
    if "pendenc" in q or "aging" in q or "overdue" in q:
        return "pendency"
    if "eefc" in q or "rollover" in q or "swap points" in q:
        return "treasury_extras"
    return None


# ---------------------------------------------------------------------------
# Palette (subset of the skill's reference palette actually used here)
# ---------------------------------------------------------------------------
SEQ_BLUE = "#2a78d6"
SEQ_BLUE_TRACK = "#b7d3f6"  # sequential step 150 -- the meter's unfilled track

STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}

DIVERGE_POS = "#2a78d6"     # blue pole -- net gain
DIVERGE_NEG = "#e34948"     # red pole -- net loss
DIVERGE_NEUTRAL = "#f0efec"

SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"

ROW_H = 22
ROW_GAP = 14
CHART_PAD_TOP = 10
LEFT_MARGIN = 4
RIGHT_MARGIN = 16
BAR_AREA_W = 380

CATEGORY_LABELS = {
    "lc_discrepancy": "LC discrepancy",
    "remittance_overdue": "Remittance overdue",
    "bg_expiring": "BG expiring soon",
}
SEVERITY_COLOR = {"medium": STATUS["warning"], "high": STATUS["critical"]}
SEVERITY_LABEL = {"medium": "Medium severity", "high": "High severity"}

AGING_BUCKETS = [
    (0, 15, "0–15 days", "warning"),
    (16, 30, "16–30 days", "warning"),
    (31, 60, "31–60 days", "serious"),
    (61, None, "60+ days", "critical"),
]


# ---------------------------------------------------------------------------
# Small formatting / escaping helpers
# ---------------------------------------------------------------------------
def _esc(text) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _fmt_amount(value, currency) -> str:
    try:
        return f"{float(value):,.0f} {currency}"
    except (TypeError, ValueError):
        return f"{value} {currency}"


def _fmt_signed_inr(value) -> str:
    v = float(value)
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:,.0f} INR"


def _bar_path(x_base: float, x_end: float, y: float, h: float, r: float = 4) -> str:
    """Horizontal bar path from x_base (baseline) to x_end (data end),
    rounded only at the data end, square at the baseline -- per the mark
    spec (marks-and-anatomy.md)."""
    r = max(0.0, min(r, h / 2, abs(x_end - x_base)))
    if r <= 0.01:
        return f"M{x_base},{y} L{x_end},{y} L{x_end},{y + h} L{x_base},{y + h} Z"
    if x_end >= x_base:
        return (
            f"M{x_base},{y} L{x_end - r},{y} "
            f"A{r},{r} 0 0 1 {x_end},{y + r} L{x_end},{y + h - r} "
            f"A{r},{r} 0 0 1 {x_end - r},{y + h} L{x_base},{y + h} Z"
        )
    return (
        f"M{x_base},{y} L{x_end + r},{y} "
        f"A{r},{r} 0 0 0 {x_end},{y + r} L{x_end},{y + h - r} "
        f"A{r},{r} 0 0 0 {x_end + r},{y + h} L{x_base},{y + h} Z"
    )


def _legend_html(entries) -> str:
    if not entries:
        return ""
    items = "".join(
        f'<span class="legend-item"><span class="swatch" style="background:{c}"></span>{_esc(l)}</span>'
        for c, l in entries
    )
    return f'<div class="legend">{items}</div>'


def _table_html(headers, rows) -> str:
    if not rows:
        return ""
    thead = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    trs = "".join(
        "<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in r) + "</tr>" for r in rows
    )
    return (
        '<details class="table-toggle"><summary>Show data table</summary>'
        f"<table><thead><tr>{thead}</tr></thead><tbody>{trs}</tbody></table></details>"
    )


def _bar_chart_svg(items, *, diverging: bool = False) -> tuple[str, int]:
    """items: list of {label, value, color, value_text, tooltip}.
    Returns (svg_markup, height_px)."""
    if not items:
        return "", 0

    label_w = min(300, max(90, max(len(it["label"]) for it in items) * 7.3 + 16))
    width = LEFT_MARGIN + label_w + BAR_AREA_W + 130 + RIGHT_MARGIN
    n = len(items)
    height = CHART_PAD_TOP + n * (ROW_H + ROW_GAP)

    bar_x0 = LEFT_MARGIN + label_w
    center_x = bar_x0 + BAR_AREA_W / 2
    max_val = max(abs(it["value"]) for it in items) or 1.0

    rows = []
    for i, it in enumerate(items):
        y = CHART_PAD_TOP + i * (ROW_H + ROW_GAP)
        row_mid = y + ROW_H / 2
        if diverging:
            half = BAR_AREA_W / 2 - 6
            frac = (it["value"] / max_val) * half
            base_x, end_x = center_x, center_x + frac
        else:
            frac = (abs(it["value"]) / max_val) * (BAR_AREA_W - 6)
            base_x, end_x = bar_x0, bar_x0 + frac
        path_d = _bar_path(base_x, end_x, y, ROW_H, r=4)

        bar_px_len = abs(end_x - base_x)
        text_w_est = len(it["value_text"]) * 6.6 + 6
        going_right = end_x >= base_x
        if diverging and bar_px_len >= text_w_est + 10:
            # Fits inside the bar near its tip -- avoids the outside label
            # colliding with the row's category label on the far (negative) side.
            anchor = "end" if going_right else "start"
            value_x = (end_x - 6) if going_right else (end_x + 6)
            value_cls = "val-label-inside"
        else:
            anchor = "start" if going_right else "end"
            value_x = end_x + (8 if going_right else -8)
            value_cls = "val-label"

        # Category label is right-aligned just before the plot area so it
        # never sits under a bar that reaches all the way to the baseline
        # (the negative side of a diverging chart can do exactly that).
        tooltip = _esc(it.get("tooltip") or f"{it['label']}: {it['value_text']}")
        rows.append(
            f'<g class="bar-row" tabindex="0" data-tooltip="{tooltip}">'
            f'<rect class="hit" x="0" y="{y - ROW_GAP / 2:.1f}" width="{width}" height="{ROW_H + ROW_GAP}" fill="transparent" />'
            f'<text x="{bar_x0 - 10:.1f}" y="{row_mid:.1f}" dominant-baseline="middle" text-anchor="end" class="cat-label">{_esc(it["label"])}</text>'
            f'<path d="{path_d}" fill="{it["color"]}" class="mark" />'
            f'<text x="{value_x:.1f}" y="{row_mid:.1f}" dominant-baseline="middle" text-anchor="{anchor}" class="{value_cls}">{_esc(it["value_text"])}</text>'
            f"</g>"
        )

    baseline_x = center_x if diverging else bar_x0
    gridline = f'<line x1="{baseline_x:.1f}" y1="{CHART_PAD_TOP - 6}" x2="{baseline_x:.1f}" y2="{height}" class="baseline" />'
    svg = (
        f'<svg viewBox="0 0 {width} {height + 6}" width="100%" height="{height + 6}" role="img">'
        f"{gridline}{''.join(rows)}</svg>"
    )
    return svg, height + 6


def _meter_svg(meters) -> tuple[str, int]:
    """meters: list of {label, pct, pct_text, tooltip}."""
    if not meters:
        return "", 0
    width = 520
    row_h = 46
    height = len(meters) * row_h
    rows = []
    for i, m in enumerate(meters):
        top = i * row_h
        track_w = width
        pct = max(0.0, min(1.0, m["pct"]))
        fill_w = max(10, track_w * pct) if pct > 0 else 0
        tooltip = _esc(m["tooltip"])
        rows.append(
            f'<g class="meter-row" tabindex="0" data-tooltip="{tooltip}">'
            f'<rect class="hit" x="0" y="{top}" width="{width}" height="{row_h}" fill="transparent" />'
            f'<text x="0" y="{top + 14}" class="meter-label">{_esc(m["label"])}</text>'
            f'<text x="{width}" y="{top + 14}" text-anchor="end" class="meter-pct">{_esc(m["pct_text"])}</text>'
            f'<rect x="0" y="{top + 22}" width="{track_w}" height="14" rx="7" fill="{SEQ_BLUE_TRACK}" />'
            + (
                f'<rect x="0" y="{top + 22}" width="{fill_w}" height="14" rx="7" fill="{SEQ_BLUE}" />'
                if fill_w > 0 else ""
            )
            + "</g>"
        )
    svg = f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img">{"".join(rows)}</svg>'
    return svg, height


# ---------------------------------------------------------------------------
# Data pulls (direct DuckDB, never LLM-generated)
# ---------------------------------------------------------------------------
def _fetch(con: duckdb.DuckDBPyConnection, sql: str) -> pd.DataFrame:
    return con.execute(sql).fetchdf()


def _fx_exposure_items(con):
    df = _fetch(con, """
        SELECT CASE WHEN sell_currency = 'INR' THEN buy_currency ELSE sell_currency END AS currency,
               SUM(amount) AS exposure, COUNT(*) AS deal_count
        FROM fx_bookings WHERE status = 'open'
        GROUP BY 1 ORDER BY 2 DESC
    """)
    items, table_rows = [], []
    for r in df.itertuples():
        vt = _fmt_amount(r.exposure, r.currency)
        items.append({
            "label": r.currency, "value": r.exposure, "color": SEQ_BLUE, "value_text": vt,
            "tooltip": f"{r.currency}: {vt} across {r.deal_count} open FX booking(s)",
        })
        table_rows.append([r.currency, vt, r.deal_count])
    return items, table_rows


def _eefc_items(con):
    # Net of any FX conversions already confirmed this session (see
    # conversion.py) -- the underlying synthetic eefc_accounts figure is
    # never mutated; this is base balance minus converted-away amount.
    balances = conversion.eefc_balances(con)
    items, table_rows = [], []
    for b in balances:
        vt = _fmt_amount(b["available"], b["currency"])
        items.append({
            "label": b["currency"], "value": b["available"], "color": SEQ_BLUE, "value_text": vt,
            "tooltip": f"EEFC-{b['currency']} balance: {vt}",
        })
        table_rows.append([b["currency"], vt])
    return items, table_rows


def _lc_meters(con):
    df = _fetch(con, """
        SELECT currency, SUM(amount) AS total_amount, SUM(COALESCE(utilized_amount, 0)) AS utilized_amount, COUNT(*) AS lc_count
        FROM letters_of_credit WHERE status != 'expired'
        GROUP BY 1 ORDER BY 2 DESC
    """)
    meters, table_rows = [], []
    for r in df.itertuples():
        pct = (r.utilized_amount / r.total_amount) if r.total_amount else 0.0
        pct_text = f"{pct * 100:.0f}%"
        util_t, tot_t = _fmt_amount(r.utilized_amount, r.currency), _fmt_amount(r.total_amount, r.currency)
        meters.append({
            "label": f"{r.currency} — {r.lc_count} active LC(s)", "pct": pct, "pct_text": pct_text,
            "tooltip": f"{r.currency}: {util_t} utilized of {tot_t} sanctioned ({pct_text})",
        })
        table_rows.append([r.currency, tot_t, util_t, pct_text, r.lc_count])
    return meters, table_rows


def _pendency_severity_items(con):
    df = _fetch(con, "SELECT category, severity, COUNT(*) AS n FROM pendencies GROUP BY 1, 2 ORDER BY 1, 2")
    items, table_rows, legend_seen = [], [], {}
    for r in df.itertuples():
        cat_label = CATEGORY_LABELS.get(r.category, r.category)
        sev_label = SEVERITY_LABEL.get(r.severity, r.severity)
        color = SEVERITY_COLOR.get(r.severity, STATUS["warning"])
        legend_seen[color] = sev_label
        items.append({
            "label": f"{cat_label} — {sev_label}", "value": r.n, "color": color, "value_text": str(r.n),
            "tooltip": f"{cat_label}: {r.n} open item(s) at {sev_label.lower()}",
        })
        table_rows.append([cat_label, sev_label, r.n])
    legend = [(c, l) for c, l in legend_seen.items()]
    return items, legend, table_rows


def _pendency_aging_items(con):
    df = _fetch(con, """
        SELECT p.pendency_id, r.remittance_id, r.days_overdue, r.amount, r.currency
        FROM pendencies p JOIN remittances r ON r.remittance_id = p.reference_id
        WHERE p.category = 'remittance_overdue'
    """)
    bucket_rows = {b[2]: [] for b in AGING_BUCKETS}
    for r in df.itertuples():
        d = r.days_overdue or 0
        for lo, hi, label, _status_key in AGING_BUCKETS:
            if d >= lo and (hi is None or d <= hi):
                bucket_rows[label].append(r)
                break

    items, table_rows, legend_seen = [], [], {}
    for lo, hi, label, status_key in AGING_BUCKETS:
        rows = bucket_rows[label]
        color = STATUS[status_key]
        n = len(rows)
        detail = "; ".join(f"{x.remittance_id} ({_fmt_amount(x.amount, x.currency)}, {x.days_overdue}d)" for x in rows)
        legend_seen[color] = status_key.capitalize()
        items.append({
            "label": label, "value": n, "color": color, "value_text": str(n),
            "tooltip": f"{label}: {n} overdue remittance(s)" + (f" — {detail}" if detail else ""),
        })
        for x in rows:
            table_rows.append([label, x.remittance_id, _fmt_amount(x.amount, x.currency), x.days_overdue])
    legend = [(c, l) for c, l in legend_seen.items()]
    return items, legend, table_rows


def _rollover_pnl_items(con):
    df = _fetch(con, """
        SELECT rollover_id, currency, swap_points_pnl_inr, bank_fee_inr, net_pnl_inr
        FROM fx_rollovers ORDER BY net_pnl_inr ASC
    """)
    items, table_rows = [], []
    for r in df.itertuples():
        color = DIVERGE_POS if r.net_pnl_inr >= 0 else DIVERGE_NEG
        vt = _fmt_signed_inr(r.net_pnl_inr)
        tooltip = (
            f"{r.rollover_id} ({r.currency}): swap points {_fmt_signed_inr(r.swap_points_pnl_inr)}, "
            f"bank fee -{r.bank_fee_inr:,.0f} INR, net {vt}"
        )
        items.append({"label": r.rollover_id, "value": r.net_pnl_inr, "color": color, "value_text": vt, "tooltip": tooltip})
        table_rows.append([
            r.rollover_id, r.currency, _fmt_signed_inr(r.swap_points_pnl_inr),
            f"-{r.bank_fee_inr:,.0f} INR", vt,
        ])
    return items, table_rows


# ---------------------------------------------------------------------------
# Card builders -- one per chart group, each returns an HTML fragment
# ---------------------------------------------------------------------------
def _fx_exposure_card(con) -> str:
    items, table_rows = _fx_exposure_items(con)
    svg, _ = _bar_chart_svg(items)
    body = svg or '<div class="empty">No open FX bookings right now.</div>'
    table = _table_html(["Currency", "Open exposure", "Open deals"], table_rows)
    return (
        '<div class="chart-card"><div class="chart-title">FX exposure by currency</div>'
        '<div class="chart-subtitle">Notional of currently open FX bookings (spot/forward), by foreign currency leg</div>'
        f"{body}</div>{table}"
    )


def _lc_utilization_card(con) -> str:
    meters, table_rows = _lc_meters(con)
    svg, _ = _meter_svg(meters)
    body = svg or '<div class="empty">No active letters of credit.</div>'
    table = _table_html(["Currency", "Sanctioned", "Utilized", "Utilized %", "Active LCs"], table_rows)
    return (
        '<div class="chart-card"><div class="chart-title">LC utilization by currency</div>'
        '<div class="chart-subtitle">Utilized vs. sanctioned amount across non-expired letters of credit</div>'
        f"{body}</div>{table}"
    )


def _pendency_card(con) -> str:
    sev_items, sev_legend, sev_rows = _pendency_severity_items(con)
    sev_svg, _ = _bar_chart_svg(sev_items)
    sev_body = (_legend_html(sev_legend) + sev_svg) if sev_items else '<div class="empty">No open pendencies — nothing flagged right now.</div>'
    sev_table = _table_html(["Category", "Severity", "Count"], sev_rows)

    age_items, age_legend, age_rows = _pendency_aging_items(con)
    age_svg, _ = _bar_chart_svg(age_items)
    age_body = (_legend_html(age_legend) + age_svg) if any(it["value"] for it in age_items) else '<div class="empty">No overdue export remittances right now.</div>'
    age_table = _table_html(["Aging bucket", "Remittance", "Amount", "Days overdue"], age_rows)

    return (
        '<div class="chart-card"><div class="chart-title">Pendencies by category &amp; severity</div>'
        '<div class="chart-subtitle">All open pendencies (LC discrepancies, overdue remittances, BGs expiring soon)</div>'
        f"{sev_body}</div>{sev_table}"
        '<div class="chart-card"><div class="chart-title">Overdue remittance aging</div>'
        '<div class="chart-subtitle">Export remittances currently overdue, bucketed by how overdue</div>'
        f"{age_body}</div>{age_table}"
    )


def _treasury_extras_card(con) -> str:
    eefc_items, eefc_rows = _eefc_items(con)
    eefc_svg, _ = _bar_chart_svg(eefc_items)
    eefc_body = eefc_svg or '<div class="empty">No EEFC balances.</div>'
    eefc_table = _table_html(["Currency", "Current balance"], eefc_rows)

    roll_items, roll_rows = _rollover_pnl_items(con)
    roll_svg, _ = _bar_chart_svg(roll_items, diverging=True)
    roll_legend = _legend_html([(DIVERGE_POS, "Net gain"), (DIVERGE_NEG, "Net loss")]) if roll_items else ""
    roll_body = (roll_legend + roll_svg) if roll_items else '<div class="empty">No FX hedges have been rolled over yet.</div>'
    roll_table = _table_html(["Rollover", "Currency", "Swap points", "Bank fee", "Net P&amp;L"], roll_rows)

    return (
        '<div class="chart-card"><div class="chart-title">EEFC balances by currency</div>'
        '<div class="chart-subtitle">Current retained foreign-currency balances</div>'
        f"{eefc_body}</div>{eefc_table}"
        '<div class="chart-card"><div class="chart-title">FX rollover P&amp;L</div>'
        '<div class="chart-subtitle">Net effect of each hedge rollover: swap-points gain/loss net of the bank’s fee</div>'
        f"{roll_body}</div>{roll_table}"
    )


_CARD_BUILDERS = {
    "fx_exposure": _fx_exposure_card,
    "lc_utilization": _lc_utilization_card,
    "pendency": _pendency_card,
    "treasury_extras": _treasury_extras_card,
}


# ---------------------------------------------------------------------------
# Page assembly
# ---------------------------------------------------------------------------
_STYLE_AND_SCRIPT = f"""
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 4px 4px 20px; font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
          background: {SURFACE}; color: {INK_PRIMARY}; }}
  .chart-card {{ padding: 6px 14px 4px; }}
  .chart-title {{ font-size: 15px; font-weight: 600; margin-top: 6px; }}
  .chart-subtitle {{ font-size: 12px; color: {INK_SECONDARY}; margin: 2px 0 10px; }}
  .cat-label {{ font-size: 12px; fill: {INK_SECONDARY}; }}
  .val-label {{ font-size: 12px; fill: {INK_PRIMARY}; font-variant-numeric: tabular-nums; }}
  .val-label-inside {{ font-size: 12px; fill: #ffffff; font-weight: 600; font-variant-numeric: tabular-nums; }}
  .meter-label {{ font-size: 12px; fill: {INK_SECONDARY}; }}
  .meter-pct {{ font-size: 12px; fill: {INK_PRIMARY}; font-weight: 600; font-variant-numeric: tabular-nums; }}
  .baseline {{ stroke: {BASELINE}; stroke-width: 1; }}
  .bar-row .mark {{ transition: opacity 0.1s; }}
  .bar-row:hover .mark, .bar-row:focus .mark {{ opacity: 0.82; }}
  .meter-row:hover, .meter-row:focus {{ outline: none; }}
  .legend {{ display: flex; gap: 16px; font-size: 12px; color: {INK_SECONDARY}; margin: 0 0 10px; flex-wrap: wrap; }}
  .legend .swatch {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: middle; }}
  .empty {{ padding: 10px 0 18px; color: {INK_MUTED}; font-size: 13px; }}
  details.table-toggle {{ margin: 0 14px 18px; font-size: 12px; color: {INK_SECONDARY}; }}
  details.table-toggle summary {{ cursor: pointer; color: {SEQ_BLUE}; }}
  details.table-toggle table {{ border-collapse: collapse; margin-top: 8px; font-size: 12px; font-variant-numeric: tabular-nums; }}
  details.table-toggle td, details.table-toggle th {{ padding: 4px 14px 4px 0; text-align: left; border-bottom: 1px solid {GRID}; white-space: nowrap; }}
  details.table-toggle th {{ color: {INK_MUTED}; font-weight: 600; }}
  hr.sep {{ border: none; border-top: 1px solid {GRID}; margin: 4px 14px 12px; }}
  #tooltip {{ position: fixed; pointer-events: none; background: {INK_PRIMARY}; color: #fff; font-size: 12px;
              padding: 6px 9px; border-radius: 6px; max-width: 300px; line-height: 1.4; opacity: 0;
              transform: translate(-50%, -115%); transition: opacity 0.08s; z-index: 999; }}
</style>
<div id="tooltip"></div>
<script>
  const tip = document.getElementById('tooltip');
  function showTip(e, text) {{
    tip.textContent = text;
    tip.style.opacity = '1';
    tip.style.left = e.clientX + 'px';
    tip.style.top = e.clientY + 'px';
  }}
  function hideTip() {{ tip.style.opacity = '0'; }}
  // The chart markup is appended AFTER this script tag in the page, so wait
  // for the DOM to finish parsing before wiring up hover/focus listeners --
  // otherwise querySelectorAll runs too early and finds nothing.
  document.addEventListener('DOMContentLoaded', function() {{
    document.querySelectorAll('.bar-row, .meter-row').forEach(function(row) {{
      const text = row.getAttribute('data-tooltip');
      row.addEventListener('pointermove', function(e) {{ showTip(e, text); }});
      row.addEventListener('pointerleave', hideTip);
      row.addEventListener('focus', function(e) {{ showTip(e, text); }});
      row.addEventListener('blur', hideTip);
    }});
  }});
</script>
"""


def render_dashboard_html(con: duckdb.DuckDBPyConnection) -> str:
    """All four chart groups stacked in one page, for the sidebar dashboard."""
    sections = []
    for i, group in enumerate(CHART_GROUPS):
        if i > 0:
            sections.append('<hr class="sep" />')
        sections.append(_CARD_BUILDERS[group](con))
    return _STYLE_AND_SCRIPT + "".join(sections)


def render_group_html(con: duckdb.DuckDBPyConnection, group: str) -> str:
    """One chart group, for a chat-triggered chart below an answer."""
    if group not in _CARD_BUILDERS:
        return ""
    return _STYLE_AND_SCRIPT + _CARD_BUILDERS[group](con)


# Rough height estimate for the Streamlit iframe -- generous, since content
# scrolls within the iframe if it runs long (see app.py's _render_html).
def estimate_height(group: str | None) -> int:
    if group is None:
        return 2000
    return {"fx_exposure": 420, "lc_utilization": 380, "pendency": 760, "treasury_extras": 640}.get(group, 500)
