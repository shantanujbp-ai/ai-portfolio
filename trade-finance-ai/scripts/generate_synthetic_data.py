"""
Generates a coherent, internally-linked synthetic dataset for a fictional
trade/treasury corporate -- an exporter AND importer -- covering shipments,
letters of credit, bank guarantees, remittances, payments, FX bookings,
FX rate history, and two precomputed convenience tables (MTM valuation and
pendencies). Everything is fictional; no real company, bank, or counterparty
data is used anywhere.

This exists because no public dataset combines these trade-finance/treasury
entities in linked form -- generic banking/fraud datasets exist, but nothing
that ties a shipment to its LC to its remittance to an FX hedge the way a
real trade-ops desk would need to query it.

Grounding for realism: LC and BG fields are modeled on the real SWIFT MT700
(documentary LC) and MT760 (bank guarantee/standby LC) message structures
-- reference number, applicant/beneficiary, latest shipment date, partial
shipment terms, documents required, etc. -- rather than invented ad hoc.

Run it with:
    python scripts/generate_synthetic_data.py

Dates are generated relative to *today* (whenever you run this), so
pendency/overdue logic always looks current rather than stale. The
underlying entities and relationships are seeded for reproducibility --
re-running produces the same company names, counterparties, and deal
structure, just with dates shifted to the new "today".
"""

import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from faker import Faker

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

SEED = 42
random.seed(SEED)
fake = Faker()
Faker.seed(SEED)

TODAY = date.today()
HISTORY_MONTHS = 12
START_DATE = TODAY - timedelta(days=365)

COMPANY_NAME = "Meridian Global Trading Pvt Ltd"  # the fictional POC company
CURRENCIES = ["USD", "EUR", "GBP", "AED", "INR"]
BASE_CURRENCY = "INR"

BANKS = [
    "First Meridian Bank", "Continental Trust Bank", "Pacific Rim Bank",
    "Anglo Gulf Bank", "Silverline National Bank",
]

# Roughly the countries a mid-size Indian import/export trading company
# would realistically deal with.
COUNTERPARTY_COUNTRIES = [
    "United Arab Emirates", "United States", "Germany", "Singapore",
    "United Kingdom", "China", "South Africa", "Vietnam", "Netherlands",
    "Saudi Arabia",
]

PRODUCTS = [
    "Refined Copper Cathodes", "Cotton Yarn", "Basmati Rice", "Auto Components",
    "Industrial Bearings", "Organic Chemicals", "Textile Machinery Parts",
    "Steel Coils", "Pharmaceutical Intermediates", "Electronic Components",
]

INCOTERMS = ["FOB", "CIF", "CFR", "EXW", "DAP"]


def rand_date(start: date, end: date) -> date:
    delta_days = (end - start).days
    return start + timedelta(days=random.randint(0, max(delta_days, 0)))


# ---------------------------------------------------------------------------
# 1. Counterparties -- banks (issuing/advising) and trade counterparties
#    (buyers for exports, suppliers for imports)
# ---------------------------------------------------------------------------
def generate_counterparties():
    rows = []
    for i, bank_name in enumerate(BANKS, start=1):
        rows.append({
            "counterparty_id": f"BANK-{i:03d}",
            "name": bank_name,
            "type": "bank",
            "role": "issuing_or_advising_bank",
            "country": "India",
        })
    for i in range(1, 16):
        role = "buyer" if i <= 9 else "supplier"  # buyers for exports, suppliers for imports
        rows.append({
            "counterparty_id": f"CP-{i:03d}",
            "name": fake.company(),
            "type": "trade_counterparty",
            "role": role,
            "country": random.choice(COUNTERPARTY_COUNTRIES),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 2. Shipments -- both export and import
# ---------------------------------------------------------------------------
def generate_shipments(counterparties: pd.DataFrame):
    buyers = counterparties[counterparties["role"] == "buyer"]["counterparty_id"].tolist()
    suppliers = counterparties[counterparties["role"] == "supplier"]["counterparty_id"].tolist()

    rows = []
    shipment_i = 1
    for direction, cp_pool, count in [("export", buyers, 35), ("import", suppliers, 25)]:
        for _ in range(count):
            # Bias ~30% of shipments into the near future (a live pipeline,
            # not just closed history) so there's a meaningful number of
            # still-open FX hedges for the MTM report to value.
            if random.random() < 0.3:
                etd = rand_date(TODAY, TODAY + timedelta(days=75))
            else:
                etd = rand_date(START_DATE, TODAY)
            transit_days = random.randint(10, 45)
            eta = etd + timedelta(days=transit_days)
            shipped = etd <= TODAY
            delivered = eta <= TODAY and shipped
            if not shipped:
                status = "booked"
            elif not delivered:
                status = "in_transit"
            else:
                status = random.choices(["delivered", "delayed"], weights=[85, 15])[0]

            currency = random.choice(["USD", "EUR", "AED"])
            rows.append({
                "shipment_id": f"SHP-{shipment_i:04d}",
                "direction": direction,
                "counterparty_id": random.choice(cp_pool),
                "product": random.choice(PRODUCTS),
                "quantity_mt": round(random.uniform(5, 500), 1),
                "incoterm": random.choice(INCOTERMS),
                "origin_port": fake.city() if direction == "import" else "Nhava Sheva, India",
                "destination_port": "Nhava Sheva, India" if direction == "import" else fake.city(),
                "etd": etd.isoformat(),
                "eta": eta.isoformat(),
                "status": status,
                "invoice_value": round(random.uniform(20000, 850000), 2),
                "currency": currency,
            })
            shipment_i += 1
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 3. Letters of Credit -- modeled on real MT700 fields. Roughly half of
#    shipments are LC-backed; the rest settle by direct/advance payment.
# ---------------------------------------------------------------------------
def generate_lcs(shipments: pd.DataFrame, counterparties: pd.DataFrame):
    banks = counterparties[counterparties["type"] == "bank"]["counterparty_id"].tolist()
    lc_backed_shipments = shipments.sample(frac=0.55, random_state=SEED)

    rows = []
    for i, shp in enumerate(lc_backed_shipments.itertuples(), start=1):
        issue_date = (date.fromisoformat(shp.etd) - timedelta(days=random.randint(20, 60)))
        latest_shipment_date = date.fromisoformat(shp.etd) + timedelta(days=random.randint(0, 5))
        expiry_date = latest_shipment_date + timedelta(days=random.randint(15, 30))

        applicant = COMPANY_NAME if shp.direction == "import" else counterparties.loc[
            counterparties["counterparty_id"] == shp.counterparty_id, "name"
        ].iloc[0]
        beneficiary = counterparties.loc[
            counterparties["counterparty_id"] == shp.counterparty_id, "name"
        ].iloc[0] if shp.direction == "import" else COMPANY_NAME

        is_past_expiry = expiry_date < TODAY
        status = random.choices(
            ["utilized", "partially_utilized", "expired", "issued"],
            weights=[55, 15, 15 if is_past_expiry else 2, 15],
        )[0]
        discrepancy = random.random() < 0.12  # ~12% of LCs have a document discrepancy

        rows.append({
            "lc_id": f"LC-{i:04d}",
            "shipment_id": shp.shipment_id,
            "direction": shp.direction,
            "applicant": applicant,
            "beneficiary": beneficiary,
            "issuing_bank": random.choice(banks),
            "advising_bank": random.choice(banks),
            "currency": shp.currency,
            "amount": shp.invoice_value,
            "issue_date": issue_date.isoformat(),
            "latest_shipment_date": latest_shipment_date.isoformat(),
            "expiry_date": expiry_date.isoformat(),
            "partial_shipment_allowed": random.choice([True, False]),
            "status": status,
            "utilized_amount": shp.invoice_value if status in ("utilized",) else (
                round(shp.invoice_value * random.uniform(0.3, 0.8), 2) if status == "partially_utilized" else 0.0
            ),
            "documents_required": "Commercial Invoice, Packing List, Bill of Lading, Certificate of Origin",
            "discrepancy_flag": discrepancy,
            "discrepancy_note": (
                "Minor discrepancy in shipping documents -- bank referral pending"
                if discrepancy else ""
            ),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 4. Bank Guarantees -- modeled on real MT760 fields. Tied to a contract/
#    counterparty rather than an individual shipment (that's how BGs
#    actually work -- a performance bond covers a whole contract).
# ---------------------------------------------------------------------------
def generate_bgs(counterparties: pd.DataFrame):
    banks = counterparties[counterparties["type"] == "bank"]["counterparty_id"].tolist()
    trade_cps = counterparties[counterparties["type"] == "trade_counterparty"]

    rows = []
    for i in range(1, 13):
        cp = trade_cps.sample(1, random_state=SEED + i).iloc[0]
        issue_date = rand_date(START_DATE, TODAY)
        validity_months = random.choice([6, 12, 18, 24])
        expiry_date = issue_date + timedelta(days=validity_months * 30)
        bg_type = random.choice(["performance", "bid_bond", "financial", "advance_payment"])
        is_expiring_soon = 0 <= (expiry_date - TODAY).days <= 30
        status = "expired" if expiry_date < TODAY else (
            "claimed" if random.random() < 0.05 else "active"
        )

        rows.append({
            "bg_id": f"BG-{i:04d}",
            "type": bg_type,
            "applicant": COMPANY_NAME,
            "beneficiary": cp["name"],
            "issuing_bank": random.choice(banks),
            "currency": random.choice(["USD", "INR", "EUR"]),
            "amount": round(random.uniform(50000, 500000), 2),
            "issue_date": issue_date.isoformat(),
            "expiry_date": expiry_date.isoformat(),
            "status": status,
            "claim_amount": round(random.uniform(10000, 100000), 2) if status == "claimed" else 0.0,
            "expiring_within_30_days": is_expiring_soon and status == "active",
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 5. Remittances -- inward proceeds for EXPORT shipments
# ---------------------------------------------------------------------------
def generate_remittances(shipments: pd.DataFrame, lcs: pd.DataFrame, counterparties: pd.DataFrame):
    banks = counterparties[counterparties["type"] == "bank"]["counterparty_id"].tolist()
    export_shipments = shipments[shipments["direction"] == "export"]
    lc_shipment_ids = set(lcs["shipment_id"])

    rows = []
    for i, shp in enumerate(export_shipments.itertuples(), start=1):
        if shp.status == "booked":
            continue  # nothing to remit yet -- hasn't shipped
        mode = "lc_backed" if shp.shipment_id in lc_shipment_ids else random.choice(["direct", "advance"])
        expected_date = date.fromisoformat(shp.eta) + timedelta(days=random.randint(5, 20))
        days_since_expected = (TODAY - expected_date).days
        # Most remittances that should have arrived by now have -- a
        # deliberate minority are overdue, which is exactly what the
        # pendencies table below surfaces. Realistically, anything more
        # than ~75 days overdue would already have been escalated/resolved
        # or written off well before now, so we don't let the random walk
        # produce implausible year-old "pending" receivables.
        if days_since_expected > 90:
            is_realized = True
        elif days_since_expected > 0:
            is_realized = random.random() < 0.6
        else:
            is_realized = False  # not due yet
        actual_date = expected_date + timedelta(days=random.randint(-3, 3)) if is_realized else None
        overdue = (not is_realized) and days_since_expected > 0

        rows.append({
            "remittance_id": f"RMT-{i:04d}",
            "shipment_id": shp.shipment_id,
            "mode": mode,
            "counterparty_bank": random.choice(banks),
            "currency": shp.currency,
            "amount": shp.invoice_value,
            "expected_date": expected_date.isoformat(),
            "actual_date": actual_date.isoformat() if actual_date else "",
            "status": "realized" if is_realized else ("overdue" if overdue else "pending"),
            "days_overdue": max((TODAY - expected_date).days, 0) if overdue else 0,
            "_days_since_expected": days_since_expected,  # dropped before saving -- used by the safety net below
        })
    df = pd.DataFrame(rows)

    # Safety net: this is randomness-driven, so a given run could plausibly
    # land zero overdue remittances by chance -- and a demo/POC with zero
    # pendencies to show is a much worse experience than one with a
    # realistic few. If that happens, deterministically flip the 1-3
    # best-fit candidates (already realized, but only just -- a small
    # days-since-expected -- so the flip still looks plausible) to overdue.
    MIN_OVERDUE_FOR_DEMO = 3
    n_overdue = (df["status"] == "overdue").sum()
    if n_overdue < MIN_OVERDUE_FOR_DEMO:
        candidates = df[
            (df["status"] == "realized") & (df["_days_since_expected"].between(10, 60))
        ].sort_values("_days_since_expected").head(MIN_OVERDUE_FOR_DEMO - n_overdue)
        for idx in candidates.index:
            days = int(df.loc[idx, "_days_since_expected"])
            df.loc[idx, "status"] = "overdue"
            df.loc[idx, "actual_date"] = ""
            df.loc[idx, "days_overdue"] = days

    return df.drop(columns=["_days_since_expected"])


# ---------------------------------------------------------------------------
# 6. Payments -- outward payments for IMPORT shipments
# ---------------------------------------------------------------------------
def generate_payments(shipments: pd.DataFrame, lcs: pd.DataFrame, counterparties: pd.DataFrame):
    banks = counterparties[counterparties["type"] == "bank"]["counterparty_id"].tolist()
    import_shipments = shipments[shipments["direction"] == "import"]
    lc_shipment_ids = set(lcs["shipment_id"])

    rows = []
    for i, shp in enumerate(import_shipments.itertuples(), start=1):
        mode = "lc_backed" if shp.shipment_id in lc_shipment_ids else random.choice(["direct", "advance"])
        if mode == "advance":
            payment_date = date.fromisoformat(shp.etd) - timedelta(days=random.randint(5, 20))
        else:
            payment_date = date.fromisoformat(shp.eta) + timedelta(days=random.randint(0, 15))
        is_paid = payment_date <= TODAY

        rows.append({
            "payment_id": f"PAY-{i:04d}",
            "shipment_id": shp.shipment_id,
            "mode": mode,
            "counterparty_bank": random.choice(banks),
            "currency": shp.currency,
            "amount": shp.invoice_value,
            "payment_date": payment_date.isoformat() if is_paid else "",
            "status": "paid" if is_paid else "scheduled",
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 7. FX rate history -- daily synthetic rates (random walk) for each
#    currency pair against INR, used to value open FX bookings (MTM).
# ---------------------------------------------------------------------------
BASE_RATES = {"USD": 83.0, "EUR": 90.0, "GBP": 105.0, "AED": 22.6}


def generate_fx_rate_history():
    rows = []
    for ccy, base_rate in BASE_RATES.items():
        rate = base_rate
        d = START_DATE
        while d <= TODAY:
            rate += random.uniform(-0.15, 0.15)  # small daily random walk
            rate = max(rate, base_rate * 0.85)     # keep it in a plausible band
            rate = min(rate, base_rate * 1.15)
            rows.append({
                "date": d.isoformat(),
                "currency_pair": f"{ccy}/INR",
                "rate": round(rate, 4),
            })
            d += timedelta(days=1)
    return pd.DataFrame(rows)


def rate_on(fx_rates: pd.DataFrame, ccy: str, on_date: date) -> float:
    subset = fx_rates[(fx_rates["currency_pair"] == f"{ccy}/INR") & (fx_rates["date"] <= on_date.isoformat())]
    if subset.empty:
        return BASE_RATES.get(ccy, 80.0)
    return float(subset.sort_values("date").iloc[-1]["rate"])


# ---------------------------------------------------------------------------
# 8. FX bookings -- treasury hedges, some linked to a shipment/LC exposure,
#    some standalone.
# ---------------------------------------------------------------------------
def generate_fx_bookings(shipments: pd.DataFrame, fx_rates: pd.DataFrame, counterparties: pd.DataFrame):
    banks = counterparties[counterparties["type"] == "bank"]["counterparty_id"].tolist()
    hedgeable = shipments[shipments["currency"] != BASE_CURRENCY].sample(frac=0.5, random_state=SEED)

    rows = []
    for i, shp in enumerate(hedgeable.itertuples(), start=1):
        deal_date = date.fromisoformat(shp.etd) - timedelta(days=random.randint(10, 40))
        deal_date = max(deal_date, START_DATE)
        value_date = date.fromisoformat(shp.eta)
        deal_type = "forward" if value_date > deal_date + timedelta(days=2) else "spot"
        booked_rate = rate_on(fx_rates, shp.currency, deal_date) * random.uniform(0.995, 1.005)
        is_export = shp.direction == "export"

        rows.append({
            "fx_deal_id": f"FX-{i:04d}",
            "deal_type": deal_type,
            "sell_currency": shp.currency if is_export else BASE_CURRENCY,
            "buy_currency": BASE_CURRENCY if is_export else shp.currency,
            "amount": shp.invoice_value,
            "deal_date": deal_date.isoformat(),
            "value_date": value_date.isoformat(),
            "booked_rate": round(booked_rate, 4),
            "counterparty_bank": random.choice(banks),
            "linked_exposure_type": "shipment",
            "linked_exposure_id": shp.shipment_id,
            "status": "matured" if value_date < TODAY else "open",
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 8b. FX rollovers -- when a forward hedge matures BEFORE the underlying cash
#     flow it's hedging actually arrives (a real, common treasury situation:
#     the shipment's ETA, which the original hedge's value date was set to,
#     turns out not to match when the remittance/payment actually settles),
#     the company can roll the contract to a later value date rather than
#     scrambling to source the currency early. The economics of a roll come
#     from "swap points" -- the interest-rate differential between INR and
#     the foreign currency for the extra days -- plus a small bank fee that
#     is always a cost regardless of which way the rate differential points.
#     ANNUAL_RATES below are simplified, fixed illustrative assumptions (not
#     modeling real/time-varying rates) used only to make swap points
#     directionally realistic for this POC: INR has historically carried a
#     higher policy rate than USD/EUR/AED, so under covered interest rate
#     parity, extending a "sell foreign currency forward" (an export hedge)
#     tends to land a BETTER INR rate the further out you go -- a swap-points
#     gain for Meridian on exports, and the mirror-image (a cost) on imports,
#     net of the bank's fee either way.
# ---------------------------------------------------------------------------
ANNUAL_RATES = {"INR": 0.065, "USD": 0.050, "EUR": 0.030, "AED": 0.040}
ROLLOVER_MIN_DELAY_DAYS = 10   # only roll if the mismatch is at least this many days
ROLLOVER_PROBABILITY = 0.65    # not every eligible mismatch actually gets rolled
ROLLOVER_BANK_FEE_BPS_RANGE = (1, 3)   # bank's spread on the roll, in basis points of notional --
                                        # realistic for a corporate FX swap; keeps the fee a real
                                        # but usually secondary cost relative to the swap points move
MIN_ROLLOVERS_FOR_DEMO = 3


def generate_fx_rollovers(fx_bookings: pd.DataFrame, remittances: pd.DataFrame,
                           payments: pd.DataFrame, fx_rates: pd.DataFrame):
    rmt_settlement = {
        r.shipment_id: date.fromisoformat(r.actual_date)
        for r in remittances.itertuples() if r.status == "realized" and r.actual_date
    }
    pay_settlement = {
        p.shipment_id: date.fromisoformat(p.payment_date)
        for p in payments.itertuples() if p.status == "paid" and p.payment_date
    }

    updated_bookings = fx_bookings.to_dict("records")
    rollovers = []
    next_fx_num = max(int(b["fx_deal_id"].split("-")[1]) for b in updated_bookings) + 1
    roll_i = 1

    def try_roll(booking, force=False):
        nonlocal next_fx_num, roll_i
        if booking["status"] != "matured":
            return False
        shipment_id = booking["linked_exposure_id"]
        old_value_date = date.fromisoformat(booking["value_date"])
        settlement_date = rmt_settlement.get(shipment_id) or pay_settlement.get(shipment_id)
        if settlement_date is None:
            return False
        delay_days = (settlement_date - old_value_date).days
        if delay_days < ROLLOVER_MIN_DELAY_DAYS:
            return False
        if not force and random.random() >= ROLLOVER_PROBABILITY:
            return False

        foreign_ccy = booking["sell_currency"] if booking["sell_currency"] != BASE_CURRENCY else booking["buy_currency"]
        is_export = booking["sell_currency"] == foreign_ccy
        rate_at_roll = rate_on(fx_rates, foreign_ccy, old_value_date)
        # Swap points for the EXTRA days being added, applied to the notional.
        swap_points_per_unit = rate_at_roll * (ANNUAL_RATES["INR"] - ANNUAL_RATES[foreign_ccy]) * delay_days / 365
        notional = booking["amount"]
        if is_export:
            swap_points_pnl = swap_points_per_unit * notional
            new_rate = booking["booked_rate"] + swap_points_per_unit
        else:
            swap_points_pnl = -swap_points_per_unit * notional
            new_rate = booking["booked_rate"] + swap_points_per_unit

        fee_bps = random.uniform(*ROLLOVER_BANK_FEE_BPS_RANGE)
        bank_fee_inr = notional * rate_at_roll * (fee_bps / 10000)
        net_pnl = swap_points_pnl - bank_fee_inr

        new_value_date = settlement_date + timedelta(days=random.randint(-2, 3))
        new_fx_id = f"FX-{next_fx_num:04d}"
        next_fx_num += 1

        new_booking = dict(booking)
        new_booking.update({
            "fx_deal_id": new_fx_id,
            "deal_date": old_value_date.isoformat(),
            "value_date": new_value_date.isoformat(),
            "booked_rate": round(new_rate, 4),
            "status": "matured" if new_value_date < TODAY else "open",
        })
        updated_bookings.append(new_booking)
        booking["status"] = "rolled"

        rollovers.append({
            "rollover_id": f"ROLL-{roll_i:04d}",
            "original_fx_deal_id": booking["fx_deal_id"],
            "new_fx_deal_id": new_fx_id,
            "shipment_id": shipment_id,
            "rollover_date": old_value_date.isoformat(),
            "old_value_date": old_value_date.isoformat(),
            "new_value_date": new_value_date.isoformat(),
            "notional_amount": notional,
            "currency": foreign_ccy,
            "swap_points_pnl_inr": round(swap_points_pnl, 2),
            "bank_fee_inr": round(bank_fee_inr, 2),
            "net_pnl_inr": round(net_pnl, 2),
        })
        roll_i += 1
        return True

    eligible_snapshot = list(updated_bookings)
    for booking in eligible_snapshot:
        try_roll(booking)

    # Safety net (same pattern as the remittance-overdue one above): a POC
    # demo with zero rollovers to show is a worse experience than one with a
    # realistic few, so if pure chance produced fewer than the minimum,
    # force-roll the best-fit remaining eligible candidates (largest delay
    # first, since those are the most obviously roll-worthy).
    if len(rollovers) < MIN_ROLLOVERS_FOR_DEMO:
        candidates = []
        for booking in eligible_snapshot:
            if booking["status"] != "matured":
                continue
            shipment_id = booking["linked_exposure_id"]
            old_value_date = date.fromisoformat(booking["value_date"])
            settlement_date = rmt_settlement.get(shipment_id) or pay_settlement.get(shipment_id)
            if settlement_date and (settlement_date - old_value_date).days >= ROLLOVER_MIN_DELAY_DAYS:
                candidates.append((settlement_date - old_value_date).days, booking)
        candidates.sort(key=lambda x: -x[0])
        for _, booking in candidates:
            if len(rollovers) >= MIN_ROLLOVERS_FOR_DEMO:
                break
            try_roll(booking, force=True)

    return pd.DataFrame(updated_bookings), pd.DataFrame(rollovers)


# ---------------------------------------------------------------------------
# 9. MTM report -- revalues each OPEN FX booking against today's market
#    rate. This is precomputed here for convenience (real treasury desks
#    run this as a periodic report); the underlying fx_bookings/fx_rates
#    tables are still there for deeper ad hoc questions.
# ---------------------------------------------------------------------------
def generate_mtm_report(fx_bookings: pd.DataFrame, fx_rates: pd.DataFrame):
    rows = []
    open_deals = fx_bookings[fx_bookings["status"] == "open"]
    for deal in open_deals.itertuples():
        foreign_ccy = deal.sell_currency if deal.sell_currency != BASE_CURRENCY else deal.buy_currency
        market_rate = rate_on(fx_rates, foreign_ccy, TODAY)
        is_export = deal.sell_currency == foreign_ccy  # selling foreign currency = export hedge
        # Simple MTM: gain if the market has moved in the company's favor
        # versus the rate it locked in.
        if is_export:
            mtm_gain_loss = (market_rate - deal.booked_rate) * deal.amount
        else:
            mtm_gain_loss = (deal.booked_rate - market_rate) * deal.amount
        rows.append({
            "fx_deal_id": deal.fx_deal_id,
            "valuation_date": TODAY.isoformat(),
            "booked_rate": deal.booked_rate,
            "market_rate": round(market_rate, 4),
            "mtm_gain_loss_inr": round(mtm_gain_loss, 2),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 10. Pendencies -- overdue/action-required items across LCs, remittances,
#     and BGs (the definition we agreed: exceptions needing attention, not
#     every still-open item).
# ---------------------------------------------------------------------------
def generate_pendencies(lcs: pd.DataFrame, remittances: pd.DataFrame, bgs: pd.DataFrame):
    rows = []
    pid = 1

    for lc in lcs[lcs["discrepancy_flag"]].itertuples():
        rows.append({
            "pendency_id": f"PND-{pid:04d}", "category": "lc_discrepancy",
            "reference_id": lc.lc_id, "description": lc.discrepancy_note,
            "severity": "medium",
        })
        pid += 1

    for r in remittances[remittances["status"] == "overdue"].itertuples():
        severity = "high" if r.days_overdue > 30 else "medium"
        rows.append({
            "pendency_id": f"PND-{pid:04d}", "category": "remittance_overdue",
            "reference_id": r.remittance_id,
            "description": f"Export remittance overdue by {r.days_overdue} days",
            "severity": severity,
        })
        pid += 1

    for bg in bgs[bgs["expiring_within_30_days"]].itertuples():
        rows.append({
            "pendency_id": f"PND-{pid:04d}", "category": "bg_expiring",
            "reference_id": bg.bg_id,
            "description": f"Bank guarantee expires {bg.expiry_date} -- renew or release",
            "severity": "medium",
        })
        pid += 1

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 11. Fund utilization -- how remittance proceeds and outward payments are
#     actually used, tying in the real treasury mechanics: EEFC retention,
#     export pre-shipment financing (packing credit), and import-side
#     financing (import loan / buyer's credit / trust receipt). These are
#     genuinely two different worlds:
#       EXPORT side:  packing credit disbursed BEFORE shipment to fund
#                     production -> shipment happens -> remittance arrives
#                     -> proceeds repay the packing credit; separately, the
#                     exporter chooses to either retain the FX in an EEFC
#                     account or convert it to INR.
#       IMPORT side:  an outward payment is funded either by a fresh FX
#                     booking/conversion, by drawing down an existing EEFC
#                     balance (skips a fresh conversion), or by drawing an
#                     import loan / buyer's credit / trust receipt that the
#                     company repays later out of its own funds.
#     Only REALIZED remittances and PAID payments participate here -- an
#     overdue or pending item hasn't actually moved money yet, so there's
#     nothing to utilize. This is a simplified, illustrative reconciliation
#     for demo purposes, not strict double-entry accounting.
# ---------------------------------------------------------------------------
EEFC_CURRENCIES = ["USD", "EUR", "AED"]  # matches the currencies shipments actually use


def generate_fund_utilization_and_facilities(shipments, remittances, payments, fx_bookings, fx_rates):
    eefc_balance = {ccy: 0.0 for ccy in EEFC_CURRENCIES}
    eefc_totals = {ccy: {"retained": 0.0, "drawn": 0.0} for ccy in EEFC_CURRENCIES}
    eefc_txns = []
    fund_utilization = []
    facilities = []
    facility_i = 1
    util_i = 1
    txn_i = 1

    fx_hedge_by_shipment = {row.linked_exposure_id: row.fx_deal_id for row in fx_bookings.itertuples()}

    # --- Packing credit: disbursed BEFORE shipment for ~half of export
    # shipments, to fund production. Normally repaid from the eventual
    # export remittance proceeds (handled in the event loop below).
    packing_credit_by_shipment = {}
    for shp in shipments[shipments["direction"] == "export"].itertuples():
        if random.random() < 0.5:
            etd = date.fromisoformat(shp.etd)
            disb_date = etd - timedelta(days=random.randint(20, 60))
            tenor_days = random.randint(60, 150)
            due_date = disb_date + timedelta(days=tenor_days)
            rate = rate_on(fx_rates, shp.currency, disb_date)
            principal_inr = round(shp.invoice_value * rate * random.uniform(0.7, 0.9), 2)
            facility = {
                "facility_id": f"TFF-{facility_i:04d}", "facility_type": "packing_credit",
                "direction": "export", "linked_shipment_id": shp.shipment_id,
                "currency": "INR", "principal_amount": principal_inr,
                "disbursement_date": disb_date.isoformat(), "tenor_days": tenor_days,
                "due_date": due_date.isoformat(), "status": "outstanding",
                "repayment_date": "", "repayment_amount": 0.0, "repayment_source": "",
            }
            facilities.append(facility)
            packing_credit_by_shipment[shp.shipment_id] = facility
            facility_i += 1

    # --- Chronological timeline of money actually moving, so EEFC balances
    # are tracked in the right order (can't draw down before it's credited).
    events = []
    for r in remittances.itertuples():
        if r.status == "realized" and r.actual_date:
            events.append(("remittance", date.fromisoformat(r.actual_date), r))
    for p in payments.itertuples():
        if p.status == "paid" and p.payment_date:
            events.append(("payment", date.fromisoformat(p.payment_date), p))
    events.sort(key=lambda e: e[1])

    for kind, event_date, rec in events:
        if kind == "remittance":
            shipment_id, currency, amount = rec.shipment_id, rec.currency, rec.amount

            facility = packing_credit_by_shipment.get(shipment_id)
            if facility and facility["status"] == "outstanding":
                facility["status"] = "repaid"
                facility["repayment_date"] = event_date.isoformat()
                facility["repayment_amount"] = facility["principal_amount"]
                facility["repayment_source"] = rec.remittance_id
                fund_utilization.append({
                    "utilization_id": f"UTIL-{util_i:04d}", "source_type": "remittance",
                    "source_id": rec.remittance_id, "shipment_id": shipment_id,
                    "utilization_type": "repaid_packing_credit",
                    "amount": facility["principal_amount"], "currency": "INR",
                    "linked_facility_id": facility["facility_id"],
                    "linked_eefc_txn_id": "", "linked_fx_deal_id": "",
                })
                util_i += 1

            # Independent of any packing-credit repayment above: the
            # incoming FX itself is either retained in EEFC or converted.
            retain = random.random() < 0.40
            linked_eefc_txn_id = ""
            if retain:
                eefc_balance[currency] += amount
                eefc_totals[currency]["retained"] += amount
                eefc_txns.append({
                    "eefc_txn_id": f"EEFCTXN-{txn_i:04d}", "eefc_account_id": f"EEFC-{currency}",
                    "date": event_date.isoformat(), "txn_type": "retention_credit", "amount": amount,
                    "linked_source_type": "remittance", "linked_source_id": rec.remittance_id,
                    "running_balance": round(eefc_balance[currency], 2),
                })
                linked_eefc_txn_id = eefc_txns[-1]["eefc_txn_id"]
                txn_i += 1

            fund_utilization.append({
                "utilization_id": f"UTIL-{util_i:04d}", "source_type": "remittance",
                "source_id": rec.remittance_id, "shipment_id": shipment_id,
                "utilization_type": "retained_eefc" if retain else "converted_to_inr",
                "amount": amount, "currency": currency,
                "linked_facility_id": "", "linked_eefc_txn_id": linked_eefc_txn_id,
                "linked_fx_deal_id": "",
            })
            util_i += 1

        else:  # payment
            shipment_id, currency, amount = rec.shipment_id, rec.currency, rec.amount
            can_draw_eefc = eefc_balance[currency] >= amount
            r = random.random()
            if r < 0.50:
                funding = "fx_booking"
            elif r < 0.70:
                funding = "eefc_drawdown" if can_draw_eefc else "fx_booking"
            else:
                funding = "import_loan"

            linked_facility_id = linked_eefc_txn_id = linked_fx_deal_id = ""

            if funding == "eefc_drawdown":
                eefc_balance[currency] -= amount
                eefc_totals[currency]["drawn"] += amount
                eefc_txns.append({
                    "eefc_txn_id": f"EEFCTXN-{txn_i:04d}", "eefc_account_id": f"EEFC-{currency}",
                    "date": event_date.isoformat(), "txn_type": "import_drawdown", "amount": -amount,
                    "linked_source_type": "payment", "linked_source_id": rec.payment_id,
                    "running_balance": round(eefc_balance[currency], 2),
                })
                linked_eefc_txn_id = eefc_txns[-1]["eefc_txn_id"]
                txn_i += 1
            elif funding == "fx_booking":
                linked_fx_deal_id = fx_hedge_by_shipment.get(shipment_id, "")
            else:  # import_loan
                tenor_days = random.randint(60, 180)
                due_date = event_date + timedelta(days=tenor_days)
                if due_date < TODAY:
                    status = "repaid" if random.random() < 0.9 else "overdue"
                    repayment_date = (due_date + timedelta(days=random.randint(-5, 10))).isoformat() if status == "repaid" else ""
                    repayment_amount = amount if status == "repaid" else 0.0
                else:
                    status, repayment_date, repayment_amount = "outstanding", "", 0.0
                facility = {
                    "facility_id": f"TFF-{facility_i:04d}",
                    "facility_type": random.choice(["import_loan", "buyers_credit", "trust_receipt"]),
                    "direction": "import", "linked_shipment_id": shipment_id,
                    "currency": currency, "principal_amount": amount,
                    "disbursement_date": event_date.isoformat(), "tenor_days": tenor_days,
                    "due_date": due_date.isoformat(), "status": status,
                    "repayment_date": repayment_date, "repayment_amount": repayment_amount,
                    "repayment_source": "own_funds" if status == "repaid" else "",
                }
                facilities.append(facility)
                linked_facility_id = facility["facility_id"]
                facility_i += 1

            fund_utilization.append({
                "utilization_id": f"UTIL-{util_i:04d}", "source_type": "payment",
                "source_id": rec.payment_id, "shipment_id": shipment_id,
                "utilization_type": f"funded_by_{funding}",
                "amount": amount, "currency": currency,
                "linked_facility_id": linked_facility_id,
                "linked_eefc_txn_id": linked_eefc_txn_id,
                "linked_fx_deal_id": linked_fx_deal_id,
            })
            util_i += 1

    eefc_accounts_rows = [{
        "eefc_account_id": f"EEFC-{ccy}", "currency": ccy,
        "total_retained": round(eefc_totals[ccy]["retained"], 2),
        "total_drawn_for_imports": round(eefc_totals[ccy]["drawn"], 2),
        "current_balance": round(eefc_balance[ccy], 2),
    } for ccy in EEFC_CURRENCIES]

    return (
        pd.DataFrame(fund_utilization),
        pd.DataFrame(eefc_txns),
        pd.DataFrame(eefc_accounts_rows),
        pd.DataFrame(facilities),
    )


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    counterparties = generate_counterparties()
    shipments = generate_shipments(counterparties)
    lcs = generate_lcs(shipments, counterparties)
    bgs = generate_bgs(counterparties)
    remittances = generate_remittances(shipments, lcs, counterparties)
    payments = generate_payments(shipments, lcs, counterparties)
    fx_rates = generate_fx_rate_history()
    fx_bookings = generate_fx_bookings(shipments, fx_rates, counterparties)
    fx_bookings, fx_rollovers = generate_fx_rollovers(fx_bookings, remittances, payments, fx_rates)
    mtm_report = generate_mtm_report(fx_bookings, fx_rates)
    pendencies = generate_pendencies(lcs, remittances, bgs)
    fund_utilization, eefc_transactions, eefc_accounts, trade_finance_facilities = (
        generate_fund_utilization_and_facilities(shipments, remittances, payments, fx_bookings, fx_rates)
    )

    tables = {
        "counterparties": counterparties,
        "shipments": shipments,
        "letters_of_credit": lcs,
        "bank_guarantees": bgs,
        "remittances": remittances,
        "payments": payments,
        "fx_rate_history": fx_rates,
        "fx_bookings": fx_bookings,
        "fx_rollovers": fx_rollovers,
        "mtm_report": mtm_report,
        "pendencies": pendencies,
        "fund_utilization": fund_utilization,
        "eefc_transactions": eefc_transactions,
        "eefc_accounts": eefc_accounts,
        "trade_finance_facilities": trade_finance_facilities,
    }
    for name, df in tables.items():
        out_path = OUTPUT_DIR / f"{name}.csv"
        df.to_csv(out_path, index=False)
        print(f"  -> {name}: {len(df)} rows written to {out_path.relative_to(OUTPUT_DIR.parent.parent)}")

    print(f"\nSynthetic data for '{COMPANY_NAME}' generated -- {HISTORY_MONTHS} months "
          f"of activity through {TODAY.isoformat()}.")


if __name__ == "__main__":
    main()
