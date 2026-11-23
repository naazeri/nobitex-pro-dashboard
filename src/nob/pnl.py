import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict

from nob.time_utils import IRST, iso_to_irst_date_str, now_irst

TEHRAN_TZ = IRST

ZERO = Decimal("0")
ONE = Decimal("1")
TEN = Decimal("10")
HUNDRED = Decimal("100")
EPSILON_QTY = Decimal("1e-9")
EPSILON_PNL = Decimal("0.001")
EPSILON_DAY = Decimal("0.005")
EPSILON_ALLOC = Decimal("0.01")

_EXCLUDED_CASHFLOW_STATUSES = {
    "canceled",
    "cancelled",
    "rejected",
    "failed",
    "expired",
    "unconfirmed",
}


def _iso_epoch(ts: Any) -> float:
    """Parse an ISO-8601 timestamp into UTC epoch seconds for deterministic event sorting."""
    if not ts:
        return 0.0
    try:
        dt = datetime.datetime.fromisoformat(str(ts).strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


def _extract_crypto_cashflow_events(
    cashflows: Optional[List[Dict[str, Any]]],
) -> Tuple[Dict[str, Decimal], Dict[str, List[Dict[str, Any]]]]:
    """
    Aggregate net externally deposited quantities and confirmed crypto withdrawal events
    per non-cash crypto symbol, skipping canceled/rejected/unconfirmed rows.
    """
    ext_deposited_by_symbol: Dict[str, Decimal] = defaultdict(lambda: ZERO)
    withdrawals_by_symbol: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    excluded_cash = {"RLS", "USDT", "DAI", "IRR", "IRT"}
    for cf in (cashflows or []):
        status_cf = str(cf.get("status") or "").strip().lower()
        if status_cf in _EXCLUDED_CASHFLOW_STATUSES:
            continue
        curr = str(cf.get("currency", "")).strip().upper()
        if not curr or curr in excluded_cash:
            continue
        ftype = str(cf.get("flow_type", "")).strip().lower()
        amt = D(cf.get("amount"))
        if amt <= ZERO:
            continue
        if ftype == "deposit":
            ext_deposited_by_symbol[curr] += amt
        elif ftype == "withdraw":
            ext_deposited_by_symbol[curr] -= amt
            withdrawals_by_symbol[curr].append(cf)
    return ext_deposited_by_symbol, withdrawals_by_symbol


def _merge_symbol_events(
    symbol_trades: List[Dict[str, Any]],
    symbol_withdrawals: List[Dict[str, Any]],
) -> List[Tuple[str, Dict[str, Any]]]:
    """Return ('trade' | 'withdraw', record) sorted chronologically (trades before withdrawals on ties)."""
    tagged: List[Tuple[float, int, str, str, Dict[str, Any]]] = []
    for t in symbol_trades:
        tagged.append((_iso_epoch(t.get("timestamp")), 0, str(t.get("id", "")), "trade", t))
    for w in symbol_withdrawals:
        tagged.append((_iso_epoch(w.get("timestamp")), 1, str(w.get("id", "")), "withdraw", w))
    tagged.sort(key=lambda item: (item[0], item[1], item[2]))
    return [(kind, rec) for _, _, _, kind, rec in tagged]


def D(val: Any, default: str = "0") -> Decimal:
    """
    Safely parse any value (str, int, float, Decimal, None) into a finite Decimal.
    Falls back to Decimal(default) for None, empty strings, NaN, or Infinity.
    """
    if isinstance(val, Decimal):
        return val if val.is_finite() else Decimal(default)
    if val is None or val == "":
        return Decimal(default)
    try:
        d = Decimal(str(val).strip())
        return d if d.is_finite() else Decimal(default)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def _round_dec(val: Decimal, places: int = 2) -> Decimal:
    """Round a Decimal to `places` decimal places using standard financial half-up rounding."""
    if not val.is_finite():
        return ZERO
    if places <= 0:
        return val.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    fmt = Decimal("1." + ("0" * places))
    return val.quantize(fmt, rounding=ROUND_HALF_UP)


def _to_float(val: Decimal, places: Optional[int] = None) -> float:
    """Convert Decimal to float at JSON serialization boundary, optionally quantized."""
    if places is not None:
        return float(_round_dec(val, places))
    return float(val)


def to_tehran_date_str(timestamp_str: str) -> str:
    """Convert UTC ISO timestamp to YYYY-MM-DD in IRST (UTC+03:30, no DST)."""
    return iso_to_irst_date_str(timestamp_str)


def _get_usdt_rls_rate(market_stats: Optional[Dict[str, Any]]) -> Decimal:
    """Return live USDT-RLS price as Decimal from market_stats, or ZERO if unavailable."""
    if not market_stats:
        return ZERO
    for key in ("usdt-rls", "usdtrls", "USDT-RLS"):
        entry = market_stats.get(key)
        if isinstance(entry, dict):
            val = D(entry.get("latest"))
            if val > ZERO:
                return val
    return ZERO


def _convert_quote_to_usdt(
    value: Decimal,
    quote: str,
    usdt_rls_rate: Decimal,
) -> Tuple[Optional[Decimal], bool]:
    """
    Convert a price/total/fee denominated in quote currency to USDT using Decimal arithmetic.
    Returns (usdt_value, is_approximate_rls_conversion).
    """
    q = (quote or "").upper()
    if q == "USDT":
        return value, False
    if q in ("RLS", "IRR"):
        if usdt_rls_rate > ZERO:
            return value / usdt_rls_rate, True
        return None, True
    if q == "IRT":
        if usdt_rls_rate > ZERO:
            return value / (usdt_rls_rate / TEN), True
        return None, True
    return None, False


def _cashflow_amount_to_usdt(
    amount: Decimal,
    currency: str,
    market_stats: Optional[Dict[str, Any]],
    usdt_rls_rate: Decimal,
) -> Decimal:
    """Convert a cashflow amount (deposit/withdrawal) in any currency to USDT using Decimal."""
    if amount <= ZERO:
        return ZERO
    curr = (currency or "").strip().upper()
    if curr in ("USDT", "DAI", "USDC"):
        return amount
    if curr in ("RLS", "IRR"):
        return (amount / usdt_rls_rate) if usdt_rls_rate > ZERO else ZERO
    if curr == "IRT":
        return (amount / (usdt_rls_rate / TEN)) if usdt_rls_rate > ZERO else ZERO
    if market_stats:
        stat = market_stats.get(f"{curr.lower()}-usdt") or market_stats.get(f"{curr.lower()}usdt")
        if isinstance(stat, dict):
            px = D(stat.get("latest"))
            if px > ZERO:
                return amount * px
        if usdt_rls_rate > ZERO:
            rls_stat = market_stats.get(f"{curr.lower()}-rls") or market_stats.get(f"{curr.lower()}rls")
            if isinstance(rls_stat, dict):
                rls_px = D(rls_stat.get("latest"))
                if rls_px > ZERO:
                    return amount * (rls_px / usdt_rls_rate)
    return ZERO


def calculate_portfolio(
    trades: List[Dict[str, Any]],
    wallets: Optional[List[Dict[str, Any]]] = None,
    market_stats: Optional[Dict[str, Any]] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    since_date: Optional[str] = None,
    cashflows: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Calculate spot portfolio metrics in USDT using exact Decimal Weighted Average Cost (WAC).

    Key Guarantees:
    - Full-History WAC Replay: Always replays full chronological history to build
      accurate current_qty and avg_cost, while scoping realized_pnl, fees, volume,
      trade counts, and timeline activity to [from_date, to_date] in IRST.
    - Multi-Quote Support: Supports both -USDT and -RLS/-IRR/-IRT spot markets
      (converting Rial markets using live usdt-rls rate and flagging approximation).
    - Margin Wallet Separation: Separates spot vs margin wallets so margin_usdt_cash
      is exposed without double-counting active position collateral.
    - Zero-Cost Sell Protection: Unmatched sells never create fake profit.
    """
    if wallets is None:
        wallets = []
    if market_stats is None:
        market_stats = {}

    effective_from = from_date or (since_date if since_date and since_date != "all" else None)
    from_bound = effective_from[:10] if effective_from else None
    to_bound = to_date[:10] if to_date else None
    has_date_filter = bool(from_bound or to_bound)

    def is_in_range(d_str: str) -> bool:
        if not has_date_filter:
            return True
        if not d_str:
            return False
        if from_bound and d_str < from_bound:
            return False
        if to_bound and d_str > to_bound:
            return False
        return True

    # Separate spot vs margin wallets (legacy entries without wallet_type default to 'spot')
    spot_wallet_map: Dict[str, Dict[str, Any]] = {}
    margin_wallet_map: Dict[str, Dict[str, Any]] = {}
    for w in wallets:
        curr = str(w.get("currency", "")).upper()
        if not curr:
            continue
        wtype = str(w.get("wallet_type") or w.get("type") or "spot").strip().lower()
        if wtype == "margin":
            margin_wallet_map[curr] = w
        else:
            spot_wallet_map[curr] = w

    usdt_rls_rate = _get_usdt_rls_rate(market_stats)

    # Group ALL trades by base crypto symbol (both -USDT and -RLS/-IRR/-IRT)
    trades_by_symbol = defaultdict(list)
    for t in trades:
        market = str(t.get("market", "")).upper()
        if "-" in market:
            base, quote = market.split("-", 1)
            if quote in ("USDT", "RLS", "IRR", "IRT") and base not in {"RLS", "USDT", "DAI", "IRR", "IRT"}:
                trades_by_symbol[base].append(t)

    # Track net externally deposited quantities & chronological crypto withdrawal events per symbol
    ext_deposited_by_symbol, withdrawals_by_symbol = _extract_crypto_cashflow_events(cashflows)

    # All unique symbols to evaluate
    all_symbols = set(trades_by_symbol.keys())
    for curr, w in spot_wallet_map.items():
        if curr not in {"RLS", "USDT", "DAI", "IRR", "IRT"}:
            if D(w.get("balance")) > ZERO:
                all_symbols.add(curr)

    holdings: Dict[str, Dict[str, Any]] = {}
    total_realized_pnl = ZERO
    total_unrealized_pnl = ZERO
    total_fees_usdt = ZERO
    total_crypto_value = ZERO
    total_invested_capital = ZERO
    total_ignored_unmatched_sells = 0

    trades_timeline = defaultdict(lambda: {"buys": 0, "sells": 0, "volume": ZERO})

    for symbol in sorted(all_symbols):
        symbol_events = _merge_symbol_events(
            trades_by_symbol.get(symbol, []),
            withdrawals_by_symbol.get(symbol, []),
        )

        current_qty = ZERO
        avg_cost = ZERO
        realized_pnl = ZERO
        asset_fees_usdt = ZERO
        total_bought_qty = ZERO
        total_bought_cost = ZERO
        total_sold_qty = ZERO
        total_sold_revenue = ZERO
        in_range_trades_count = 0
        ignored_unmatched_sells = 0
        used_approx_rls = False

        for kind, ev in symbol_events:
            if kind == "withdraw":
                amt_w = D(ev.get("amount"))
                if amt_w > ZERO:
                    removed = min(current_qty, amt_w)
                    current_qty -= removed
                    if current_qty <= EPSILON_QTY:
                        current_qty = ZERO
                        avg_cost = ZERO
                continue

            t = ev
            trade_type = str(t.get("type", "")).lower()
            market = str(t.get("market", "")).upper()
            quote = market.split("-", 1)[1] if "-" in market else "USDT"

            raw_price = D(t.get("price"))
            amount = D(t.get("amount"))
            raw_total = D(t.get("total"))
            fee_raw = D(t.get("fee"))

            price, approx_p = _convert_quote_to_usdt(raw_price, quote, usdt_rls_rate)
            total, approx_t = _convert_quote_to_usdt(raw_total, quote, usdt_rls_rate)
            if price is None or total is None:
                continue
            if approx_p or approx_t:
                used_approx_rls = True

            d_str = to_tehran_date_str(str(t.get("timestamp", "")))
            in_window = is_in_range(d_str)

            if trade_type == "buy":
                # Buy fee is charged in base cryptocurrency; convert to USDT using trade price
                fee_usdt = fee_raw * price
                net_amount = max(ZERO, amount - fee_raw)

                # Always replay WAC from full history
                denom = current_qty + net_amount
                if denom > ZERO:
                    avg_cost = ((current_qty * avg_cost) + total) / denom
                current_qty += net_amount

                if in_window:
                    asset_fees_usdt += fee_usdt
                    total_bought_qty += amount
                    total_bought_cost += total
                    in_range_trades_count += 1
                    if len(d_str) >= 7:
                        trades_timeline[d_str[:7]]["buys"] += 1
                        trades_timeline[d_str[:7]]["volume"] += total

            elif trade_type == "sell":
                # Sell fee is charged in quote currency; convert to USDT
                fee_usdt_opt, _ = _convert_quote_to_usdt(fee_raw, quote, usdt_rls_rate)
                fee_usdt = fee_usdt_opt if fee_usdt_opt is not None else ZERO

                # Always replay inventory against full history; ignore unmatched pre-180d sells completely
                matched_qty = min(current_qty, amount)
                if matched_qty <= EPSILON_QTY or avg_cost <= ZERO:
                    if in_window:
                        ignored_unmatched_sells += 1
                    continue

                ratio = (matched_qty / amount) if amount > ZERO else ZERO
                matched_cost = matched_qty * avg_cost
                matched_revenue = matched_qty * price
                prop_fee = fee_usdt * ratio
                trade_pnl = matched_revenue - matched_cost - prop_fee
                current_qty -= matched_qty
                if current_qty <= EPSILON_QTY:
                    current_qty = ZERO
                    avg_cost = ZERO

                if in_window:
                    asset_fees_usdt += prop_fee
                    total_sold_qty += matched_qty
                    total_sold_revenue += matched_revenue
                    realized_pnl += trade_pnl
                    in_range_trades_count += 1
                    if len(d_str) >= 7:
                        trades_timeline[d_str[:7]]["sells"] += 1
                        trades_timeline[d_str[:7]]["volume"] += matched_revenue

        # Check actual spot wallet balance if available
        wallet_entry = spot_wallet_map.get(symbol)
        if wallet_entry is not None:
            wallet_balance = D(wallet_entry.get("balance"))
            active_balance = D(
                wallet_entry.get("activeBalance", wallet_entry.get("active_balance")),
                default=str(wallet_balance),
            )
        elif spot_wallet_map:
            wallet_balance = ZERO
            active_balance = ZERO
        else:
            wallet_balance = current_qty
            active_balance = current_qty

        holding_qty = wallet_balance

        # When a date filter is active, omit completely inactive 0-balance symbols
        if has_date_filter and holding_qty <= EPSILON_QTY and in_range_trades_count == 0:
            continue

        # Look up current market price in USDT (fallback to RLS converted if USDT pair absent)
        market_key = f"{symbol}-usdt".lower()
        stat = market_stats.get(market_key, {})
        current_price = D(stat.get("latest"))
        day_change = D(stat.get("dayChange"))
        day_high = D(stat.get("dayHigh"))
        day_low = D(stat.get("dayLow"))

        if current_price <= ZERO and usdt_rls_rate > ZERO:
            rls_stat = market_stats.get(f"{symbol}-rls".lower(), {})
            rls_latest = D(rls_stat.get("latest"))
            if rls_latest > ZERO:
                current_price = rls_latest / usdt_rls_rate
                day_change = D(rls_stat.get("dayChange"))
                day_high = D(rls_stat.get("dayHigh")) / usdt_rls_rate
                day_low = D(rls_stat.get("dayLow")) / usdt_rls_rate

        current_value = holding_qty * current_price
        ext_dep_qty = max(ZERO, ext_deposited_by_symbol.get(symbol, ZERO))

        # Reconcile tracked cost-basis quantity vs untracked pre-180d wallet quantity
        if holding_qty <= EPSILON_QTY:
            avg_cost = ZERO
            cost_tracked_qty = ZERO
            untracked_qty = ZERO
            unrealized_pnl = ZERO
            pnl_pct = ZERO
            cost_basis_source = "flat"
        elif avg_cost > ZERO:
            excess_qty = max(ZERO, holding_qty - current_qty)
            is_partial = (
                current_qty > EPSILON_QTY
                and excess_qty > (current_qty * Decimal("0.02"))
                and (excess_qty * current_price) > ONE
            )
            if is_partial:
                cost_tracked_qty = current_qty
                untracked_qty = excess_qty
                cost_basis_source = "partial_history"
            else:
                cost_tracked_qty = holding_qty
                untracked_qty = ZERO
                cost_basis_source = "trades"

            if current_price > ZERO:
                unrealized_pnl = (current_price - avg_cost) * cost_tracked_qty
                pnl_pct = ((current_price - avg_cost) / avg_cost) * HUNDRED
            else:
                unrealized_pnl = ZERO
                pnl_pct = ZERO
            total_invested_capital += cost_tracked_qty * avg_cost
        else:
            cost_tracked_qty = ZERO
            untracked_qty = holding_qty
            unrealized_pnl = ZERO
            pnl_pct = ZERO
            cost_basis_source = "external_deposit" if ext_dep_qty > EPSILON_QTY else "missing_history"

        asset_total_pnl = realized_pnl + unrealized_pnl

        total_realized_pnl += realized_pnl
        total_unrealized_pnl += unrealized_pnl
        total_fees_usdt += asset_fees_usdt
        total_crypto_value += current_value
        total_ignored_unmatched_sells += ignored_unmatched_sells

        holdings[symbol] = {
            "symbol": symbol,
            "market": f"{symbol}-USDT",
            "quantity": _to_float(holding_qty),
            "active_quantity": _to_float(active_balance),
            "cost_tracked_qty": _to_float(cost_tracked_qty),
            "untracked_qty": _to_float(untracked_qty),
            "avg_cost": _to_float(avg_cost),
            "current_price": _to_float(current_price),
            "current_value": _to_float(current_value),
            "realized_pnl": _to_float(realized_pnl),
            "unrealized_pnl": _to_float(unrealized_pnl),
            "total_pnl": _to_float(asset_total_pnl),
            "pnl_pct": _to_float(pnl_pct),
            "day_change": _to_float(day_change),
            "day_high": _to_float(day_high),
            "day_low": _to_float(day_low),
            "fees_paid_usdt": _to_float(asset_fees_usdt),
            "trades_count": in_range_trades_count,
            "ignored_unmatched_sells": ignored_unmatched_sells,
            "total_bought_qty": _to_float(total_bought_qty),
            "total_bought_cost": _to_float(total_bought_cost),
            "total_sold_qty": _to_float(total_sold_qty),
            "total_sold_revenue": _to_float(total_sold_revenue),
            "is_dust": current_value < ONE,
            "approximate_rls_conversion": used_approx_rls,
            "external_deposited_qty": _to_float(ext_dep_qty),
            "cost_basis_source": cost_basis_source,
        }

    # Spot USDT, DAI & RLS/IRT cash reserves
    usdt_wallet = spot_wallet_map.get("USDT", {})
    usdt_cash = D(usdt_wallet.get("balance"))

    dai_wallet = spot_wallet_map.get("DAI", {})
    dai_cash = D(dai_wallet.get("balance"))

    rls_wallet = spot_wallet_map.get("RLS", {})
    rls_cash = D(rls_wallet.get("balance"))
    rls_cash_usdt = (rls_cash / usdt_rls_rate) if (rls_cash > ZERO and usdt_rls_rate > ZERO) else ZERO

    # Margin USDT cash (active_balance = free margin USDT, avoiding double-counting locked collateral)
    margin_usdt_wallet = margin_wallet_map.get("USDT", {})
    margin_usdt_total = D(margin_usdt_wallet.get("balance"))
    margin_usdt_cash = D(
        margin_usdt_wallet.get("active_balance", margin_usdt_wallet.get("activeBalance")),
        default=str(margin_usdt_total),
    )

    total_portfolio_value = total_crypto_value + usdt_cash + dai_cash + rls_cash_usdt
    total_pnl = total_realized_pnl + total_unrealized_pnl

    overall_pnl_pct = (
        (total_pnl / total_invested_capital * HUNDRED)
        if total_invested_capital > ZERO
        else ZERO
    )

    active_holdings = [h for h in holdings.values() if h["current_value"] >= 1.0]
    dust_holdings = [h for h in holdings.values() if h["current_value"] < 1.0]

    priced_active = [h for h in active_holdings if h["avg_cost"] > 0]
    best_performer = max(priced_active, key=lambda x: x["pnl_pct"]) if priced_active else None
    worst_performer = min(priced_active, key=lambda x: x["pnl_pct"]) if priced_active else None

    allocation_items = []
    if usdt_cash > EPSILON_ALLOC:
        allocation_items.append({
            "symbol": "USDT",
            "value": _to_float(usdt_cash, 2),
            "color": "#10b981",
        })
    if dai_cash > EPSILON_ALLOC:
        allocation_items.append({
            "symbol": "DAI",
            "value": _to_float(dai_cash, 2),
            "color": "#f59e0b",
        })
    if rls_cash_usdt > EPSILON_ALLOC:
        allocation_items.append({
            "symbol": "IRT",
            "value": _to_float(rls_cash_usdt, 2),
            "color": "#06b6d4",
        })

    sorted_cryptos = sorted(
        [h for h in holdings.values() if h["current_value"] > 0.01],
        key=lambda x: x["current_value"],
        reverse=True,
    )

    top_cryptos = sorted_cryptos[:8]
    other_value = sum((D(h["current_value"]) for h in sorted_cryptos[8:]), ZERO)
    for c in top_cryptos:
        allocation_items.append({
            "symbol": c["symbol"],
            "value": _to_float(D(c["current_value"]), 2),
        })
    if other_value > EPSILON_ALLOC:
        allocation_items.append({
            "symbol": "OTHERS",
            "value": _to_float(other_value, 2),
        })

    for item in allocation_items:
        item["percentage"] = (
            _to_float((D(item["value"]) / total_portfolio_value) * HUNDRED, 1)
            if total_portfolio_value > ZERO
            else 0.0
        )

    pnl_by_asset = sorted(
        [h for h in holdings.values() if abs(h["total_pnl"]) > 0.01],
        key=lambda x: abs(x["total_pnl"]),
        reverse=True,
    )[:10]

    timeline_sorted = [
        {"period": k, "buys": v["buys"], "sells": v["sells"], "volume": _to_float(v["volume"])}
        for k, v in sorted(trades_timeline.items())
    ]

    return {
        "summary": {
            "total_portfolio_value": _to_float(total_portfolio_value, 2),
            "total_crypto_value": _to_float(total_crypto_value, 2),
            "usdt_cash": _to_float(usdt_cash, 2),
            "dai_cash": _to_float(dai_cash, 2),
            "rls_cash": _to_float(rls_cash, 0),
            "irt_cash": int(_round_dec(rls_cash / TEN, 0)),
            "rls_cash_usdt": _to_float(rls_cash_usdt, 2),
            "margin_usdt_cash": _to_float(margin_usdt_cash, 2),
            "margin_usdt_total": _to_float(margin_usdt_total, 2),
            "total_invested_capital": _to_float(total_invested_capital, 2),
            "total_realized_pnl": _to_float(total_realized_pnl, 2),
            "total_unrealized_pnl": _to_float(total_unrealized_pnl, 2),
            "total_pnl": _to_float(total_pnl, 2),
            "overall_pnl_pct": _to_float(overall_pnl_pct, 2),
            "total_fees_usdt": _to_float(total_fees_usdt, 2),
            "active_assets_count": len(active_holdings),
            "dust_assets_count": len(dust_holdings),
            "total_assets_count": len(holdings),
            "ignored_unmatched_sells_count": total_ignored_unmatched_sells,
            "best_performer": best_performer,
            "worst_performer": worst_performer,
            "from_date": effective_from,
            "to_date": to_date,
            "since_date": effective_from,
        },
        "holdings": sorted(
            list(holdings.values()),
            key=lambda x: x["current_value"],
            reverse=True,
        ),
        "charts": {
            "asset_allocation": allocation_items,
            "pnl_by_asset": [
                {
                    "symbol": h["symbol"],
                    "realized": _to_float(D(h["realized_pnl"]), 2),
                    "unrealized": _to_float(D(h["unrealized_pnl"]), 2),
                    "total": _to_float(D(h["total_pnl"]), 2),
                    "pnl_pct": _to_float(D(h["pnl_pct"]), 2),
                }
                for h in pnl_by_asset
            ],
            "timeline": timeline_sorted,
        },
    }


def calculate_positions_summary(
    positions: List[Dict[str, Any]],
    market_stats: Optional[Dict[str, Any]] = None,
    trades: Optional[List[Dict[str, Any]]] = None,
    margin_markets: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Summarize leveraged positions (معاملات تعهدی) from Nobitex /positions/list using Decimal arithmetic.
    Includes comprehensive fee accounting: opening fee, closing fee, and auto-renewal (extension) fee.
    Uses live cached /margin/markets/list fee rates when falling back to estimation.
    """
    if market_stats is None:
        market_stats = {}

    if trades is None:
        try:
            from nob.db import get_margin_trades
            trades = get_margin_trades()
        except Exception:
            trades = []

    if margin_markets is None:
        try:
            from nob.db import get_metadata_json
            cached_mm = get_metadata_json("margin_markets_cache")
            margin_markets = cached_mm if isinstance(cached_mm, dict) else {}
        except Exception:
            margin_markets = {}

    # Build trade lookup maps for fast matching
    trades_by_ts: Dict[str, Dict[str, Any]] = {}
    trades_by_order: Dict[Any, List[Dict[str, Any]]] = {}
    for t in (trades or []):
        ts = t.get("timestamp")
        if ts:
            trades_by_ts[ts] = t
        oid = t.get("order_id")
        if oid is not None:
            trades_by_order.setdefault(oid, []).append(t)

    active_positions: List[Dict[str, Any]] = []
    past_positions: List[Dict[str, Any]] = []

    total_realized_pnl_usdt = ZERO
    total_realized_pnl_rls = ZERO
    wins = 0
    losses = 0

    total_fees_usdt = ZERO
    total_open_fees_usdt = ZERO
    total_close_fees_usdt = ZERO
    total_extension_fees_usdt = ZERO

    total_fees_rls = ZERO
    total_open_fees_rls = ZERO
    total_close_fees_rls = ZERO
    total_extension_fees_rls = ZERO

    default_rate_rls = Decimal("0.00125")
    default_rate_usdt = Decimal("0.0008")
    default_ext_rate = Decimal("0.00033333")

    for p in positions:
        status = str(p.get("status", "")).strip()
        src = str(p.get("src_currency", p.get("srcCurrency", ""))).upper()
        dst = str(p.get("dst_currency", p.get("dstCurrency", ""))).lower()
        side = str(p.get("side", "")).strip().lower()
        collat = D(p.get("collateral"))
        lev = D(p.get("leverage"), default="1")
        if lev <= ZERO:
            lev = ONE
        entry_p = D(p.get("entry_price"))
        exit_p = D(p.get("exit_price"))

        # Resolve live fee rates from cached /margin/markets/list with safe static fallbacks
        m_cfg = None
        if margin_markets:
            m_cfg = margin_markets.get(f"{src}{dst.upper()}")
            if m_cfg is None and dst in ("rls", "irr"):
                m_cfg = margin_markets.get(f"{src}IRT")
        rate = default_rate_rls if dst == "rls" else default_rate_usdt
        ext_rate = default_ext_rate
        if isinstance(m_cfg, dict):
            fee_key = "buyPositionFeeRate" if side == "buy" else "sellPositionFeeRate"
            live_rate = D(m_cfg.get(fee_key))
            if live_rate > ZERO:
                rate = live_rate
            live_ext = D(m_cfg.get("extensionFeeRate") or m_cfg.get("renewalFeeRate"))
            if live_ext > ZERO:
                ext_rate = live_ext

        # --- 1. Opening Fee ---
        opened_at = p.get("opened_at")
        open_fee = ZERO
        if opened_at in trades_by_ts:
            oid = trades_by_ts[opened_at].get("order_id")
            fills = trades_by_order.get(oid, [trades_by_ts[opened_at]])
            for fill in fills:
                f_price = D(fill.get("price"))
                f_fee = D(fill.get("fee"))
                if fill.get("type") == "buy":
                    open_fee += f_fee * f_price
                else:
                    open_fee += f_fee
        else:
            open_fee = collat * lev * rate

        # --- 2. Closing Fee ---
        closed_at = p.get("closed_at")
        close_fee = ZERO
        if status == "Open" or not closed_at:
            close_fee = ZERO
        elif closed_at in trades_by_ts:
            oid = trades_by_ts[closed_at].get("order_id")
            fills = trades_by_order.get(oid, [trades_by_ts[closed_at]])
            for fill in fills:
                f_price = D(fill.get("price"))
                f_fee = D(fill.get("fee"))
                if fill.get("type") == "buy":
                    close_fee += f_fee * f_price
                else:
                    close_fee += f_fee
        else:
            val_exit = (
                collat * lev * (exit_p / entry_p)
                if (entry_p > ZERO and exit_p > ZERO)
                else (collat * lev)
            )
            close_fee = val_exit * rate

        # --- 3. Extension / Auto-Renewal Fee ---
        # Note: On Open positions, Nobitex `/positions/list` reports `extensionFee` as the per-8h fee rate,
        # charged separately as `tp='fee'` wallet deductions every 8h; neither Open `unrealizedPNL` nor
        # closed `PNL` deducts extension fees, while trading open/close fees and poolPNLShare ARE already
        # deducted inside Nobitex's PNL / unrealizedPNL.
        api_ext_val = D(p.get("extension_fee") if p.get("extension_fee") not in (None, "") else p.get("extensionFee"))
        ext_fee_per_period = api_ext_val if status == "Open" else ZERO
        if status == "Open":
            if api_ext_val > ZERO and opened_at:
                try:
                    t_o = datetime.datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
                    if t_o.tzinfo is None:
                        t_o = t_o.replace(tzinfo=datetime.timezone.utc)
                    dur_hours = max(0.0, (now_irst() - t_o).total_seconds() / 3600.0)
                    periods = Decimal(max(1, int(dur_hours // 8)))
                    ext_fee = periods * api_ext_val
                except Exception:
                    ext_fee = api_ext_val
            else:
                ext_fee = max(ZERO, api_ext_val)
        else:
            ext_fee = api_ext_val
            if ext_fee <= ZERO and closed_at and opened_at:
                try:
                    t_o = datetime.datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
                    t_c = datetime.datetime.fromisoformat(str(closed_at).replace("Z", "+00:00"))
                    dur_hours = max(0.0, (t_c - t_o).total_seconds() / 3600.0)
                    periods = Decimal(int(dur_hours // 8))
                    liability = collat * max(ZERO, lev - ONE)
                    ext_fee = periods * ext_rate * liability
                except Exception:
                    ext_fee = ZERO

        # Quantize individual position fee figures
        if dst == "usdt":
            open_fee_q = _round_dec(open_fee, 4)
            close_fee_q = _round_dec(close_fee, 4)
            ext_fee_q = _round_dec(ext_fee, 4)
            total_fee_q = _round_dec(open_fee + close_fee + ext_fee, 4)

            p["open_fee"] = float(open_fee_q)
            p["close_fee"] = float(close_fee_q)
            p["extension_fee"] = float(ext_fee_q)
            p["extension_fee_per_period"] = float(_round_dec(ext_fee_per_period, 4))
            p["total_fee"] = float(total_fee_q)

            total_fees_usdt += total_fee_q
            total_open_fees_usdt += open_fee_q
            total_close_fees_usdt += close_fee_q
            total_extension_fees_usdt += ext_fee_q
        else:
            open_fee_q = _round_dec(open_fee, 0)
            close_fee_q = _round_dec(close_fee, 0)
            ext_fee_q = _round_dec(ext_fee, 0)
            total_fee_q = _round_dec(open_fee + close_fee + ext_fee, 0)

            p["open_fee"] = int(open_fee_q)
            p["close_fee"] = int(close_fee_q)
            p["extension_fee"] = int(ext_fee_q)
            p["extension_fee_per_period"] = int(_round_dec(ext_fee_per_period, 0))
            p["total_fee"] = int(total_fee_q)

            total_fees_rls += total_fee_q
            total_open_fees_rls += open_fee_q
            total_close_fees_rls += close_fee_q
            total_extension_fees_rls += ext_fee_q

        # True Net PnL: Nobitex API PNL/unrealizedPNL already excludes open/close fees and poolPNLShare;
        # only extensionFee must be deducted here to avoid double-deducting trading fees or pool share.
        api_pnl = D(p.get("pnl") if p.get("pnl") not in (None, "") else p.get("PNL"))
        pool_share = D(p.get("pool_pnl_share") if p.get("pool_pnl_share") not in (None, "") else p.get("poolPNLShare"))

        net_pnl = api_pnl - ext_fee_q
        gross_pnl = (api_pnl + open_fee_q) if status == "Open" else (api_pnl + pool_share + open_fee_q + close_fee_q)

        net_pnl_percent = (net_pnl / collat * HUNDRED) if collat > ZERO else ZERO

        if dst == "usdt":
            net_pnl_q = _round_dec(net_pnl, 4)
            p["gross_pnl"] = _to_float(gross_pnl, 4)
            p["pool_pnl_share"] = _to_float(pool_share, 4)
            p["net_pnl"] = float(net_pnl_q)
            p["pnl"] = float(net_pnl_q)
        else:
            net_pnl_q = _round_dec(net_pnl, 0)
            p["gross_pnl"] = int(_round_dec(gross_pnl, 0))
            p["pool_pnl_share"] = int(_round_dec(pool_share, 0))
            p["net_pnl"] = int(net_pnl_q)
            p["pnl"] = int(net_pnl_q)

        p["gross_pnl_percent"] = _to_float(D(p.get("pnl_percent")))
        p["net_pnl_percent"] = _to_float(net_pnl_percent, 2)
        p["pnl_percent"] = _to_float(net_pnl_percent, 2)

        if status == "Open":
            mark_p = D(p.get("mark_price"))
            current_p = mark_p if mark_p > ZERO else entry_p
            liq_p = D(p.get("liquidation_price"))

            if current_p > ZERO and liq_p > ZERO:
                dist_pct = _round_dec(abs(current_p - liq_p) / current_p * HUNDRED, 2)
                dist_amt = _round_dec(abs(current_p - liq_p), 2)
                if dist_pct >= Decimal("20"):
                    r_level = "safe"
                elif dist_pct >= Decimal("10"):
                    r_level = "warning"
                else:
                    r_level = "danger"
            else:
                dist_pct = ZERO
                dist_amt = ZERO
                r_level = "unknown"

            p["liquidation_distance_pct"] = float(dist_pct)
            p["liquidation_distance_amount"] = float(dist_amt)
            p["risk_level"] = r_level
            active_positions.append(p)
        else:
            past_positions.append(p)
            if dst == "usdt":
                total_realized_pnl_usdt += net_pnl_q
            elif dst == "rls":
                total_realized_pnl_rls += net_pnl_q

            if net_pnl_q > EPSILON_PNL:
                wins += 1
            elif net_pnl_q < -EPSILON_PNL:
                losses += 1

    total_past = len(past_positions)
    win_rate_pct = (
        _to_float((Decimal(wins) / Decimal(total_past)) * HUNDRED, 1)
        if total_past > 0
        else 0.0
    )

    usdt_past = [
        p for p in past_positions
        if str(p.get("dst_currency", p.get("dstCurrency", ""))).lower() == "usdt"
    ]
    best_position = (
        max(usdt_past, key=lambda x: D(x.get("pnl")))
        if usdt_past
        else None
    )
    worst_position = (
        min(usdt_past, key=lambda x: D(x.get("pnl")))
        if usdt_past
        else None
    )

    safe_count = sum(1 for p in active_positions if p.get("risk_level") == "safe")
    warning_count = sum(1 for p in active_positions if p.get("risk_level") == "warning")
    danger_count = sum(1 for p in active_positions if p.get("risk_level") == "danger")
    highest_risk_position = (
        min(active_positions, key=lambda x: x.get("liquidation_distance_pct", 999.0))
        if active_positions
        else None
    )

    return {
        "summary": {
            "total_realized_pnl_usdt": _to_float(total_realized_pnl_usdt, 2),
            "total_realized_pnl_rls": _to_float(total_realized_pnl_rls, 0),
            "total_realized_pnl_irt": int(_round_dec(total_realized_pnl_rls / TEN, 0)),
            "total_fees_usdt": _to_float(total_fees_usdt, 2),
            "total_open_fees_usdt": _to_float(total_open_fees_usdt, 2),
            "total_close_fees_usdt": _to_float(total_close_fees_usdt, 2),
            "total_extension_fees_usdt": _to_float(total_extension_fees_usdt, 2),
            "total_fees_rls": int(_round_dec(total_fees_rls, 0)),
            "total_fees_irt": int(_round_dec(total_fees_rls / TEN, 0)),
            "total_open_fees_irt": int(_round_dec(total_open_fees_rls / TEN, 0)),
            "total_close_fees_irt": int(_round_dec(total_close_fees_rls / TEN, 0)),
            "total_extension_fees_irt": int(_round_dec(total_extension_fees_rls / TEN, 0)),
            "active_count": len(active_positions),
            "past_count": total_past,
            "wins_count": wins,
            "losses_count": losses,
            "win_rate_pct": win_rate_pct,
            "best_position": best_position,
            "worst_position": worst_position,
            "highest_risk_position": highest_risk_position,
            "safe_count": safe_count,
            "warning_count": warning_count,
            "danger_count": danger_count,
        },
        "active": active_positions,
        "past": past_positions,
    }


def calculate_daily_pnl_calendar(
    trades: List[Dict[str, Any]],
    past_positions: List[Dict[str, Any]],
    market_stats: Optional[Dict[str, Any]] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    since_date: Optional[str] = None,
    cashflows: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Calculate daily realized PnL breakdown across Spot and Futures using Decimal arithmetic.
    Maps each trading day to its spot sells, futures closed positions, fees, volume, and net profit.
    Replays confirmed crypto withdrawals chronologically and completely ignores unmatched pre-180d sells.
    """
    effective_from = from_date or (since_date if since_date and since_date != "all" else None)
    to_cutoff = to_date[:10] if to_date else None

    def is_in_range(d: str) -> bool:
        if not d:
            return False
        if effective_from and d < effective_from[:10]:
            return False
        if to_cutoff and d > to_cutoff:
            return False
        return True

    daily_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "date": "",
        "spot_pnl": ZERO,
        "futures_pnl_usdt": ZERO,
        "futures_pnl_irt": ZERO,
        "total_pnl": ZERO,
        "volume_usdt": ZERO,
        "fees_usdt": ZERO,
        "trades_count": 0,
        "status": "neutral",
        "spot_trades": [],
        "futures_positions": [],
    })

    usdt_rls_rate = _get_usdt_rls_rate(market_stats)
    _, withdrawals_by_symbol = _extract_crypto_cashflow_events(cashflows)

    # 1. Process Spot Trades & Withdrawals Chronologically per Asset for WAC Realized PnL
    by_symbol: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in trades:
        market = str(t.get("market", "")).upper()
        symbol = market.split("-")[0] if "-" in market else market
        by_symbol[symbol].append(t)

    for symbol, symbol_trades in by_symbol.items():
        current_qty = ZERO
        avg_cost = ZERO
        symbol_events = _merge_symbol_events(symbol_trades, withdrawals_by_symbol.get(symbol, []))

        for kind, ev in symbol_events:
            if kind == "withdraw":
                amt_w = D(ev.get("amount"))
                if amt_w > ZERO:
                    removed = min(current_qty, amt_w)
                    current_qty -= removed
                    if current_qty <= EPSILON_QTY:
                        current_qty = ZERO
                        avg_cost = ZERO
                continue

            t = ev
            ttype = str(t.get("type", "")).lower()
            market = str(t.get("market", "")).upper()
            quote = market.split("-", 1)[1] if "-" in market else "USDT"

            raw_price = D(t.get("price"))
            amount = D(t.get("amount"))
            raw_total = D(t.get("total"))
            fee_raw = D(t.get("fee"))

            price, _ = _convert_quote_to_usdt(raw_price, quote, usdt_rls_rate)
            total, _ = _convert_quote_to_usdt(raw_total, quote, usdt_rls_rate)
            if price is None or total is None:
                continue

            ts = str(t.get("timestamp", ""))
            date_str = to_tehran_date_str(ts)

            if ttype == "buy":
                fee_usdt = fee_raw * price
                net_amount = max(ZERO, amount - fee_raw)
                denom = current_qty + net_amount
                if denom > ZERO:
                    avg_cost = ((current_qty * avg_cost) + total) / denom
                current_qty += net_amount

                if is_in_range(date_str):
                    daily_stats[date_str]["date"] = date_str
                    daily_stats[date_str]["volume_usdt"] += total
                    daily_stats[date_str]["fees_usdt"] += fee_usdt

            elif ttype == "sell":
                fee_usdt_opt, _ = _convert_quote_to_usdt(fee_raw, quote, usdt_rls_rate)
                fee_usdt = fee_usdt_opt if fee_usdt_opt is not None else ZERO
                matched_qty = min(current_qty, amount)

                if matched_qty <= EPSILON_QTY or avg_cost <= ZERO:
                    continue

                ratio = (matched_qty / amount) if amount > ZERO else ZERO
                entry_avg_cost = avg_cost
                matched_cost = matched_qty * entry_avg_cost
                matched_revenue = matched_qty * price
                prop_fee = fee_usdt * ratio
                trade_pnl = matched_revenue - matched_cost - prop_fee

                current_qty -= matched_qty
                if current_qty <= EPSILON_QTY:
                    current_qty = ZERO
                    avg_cost = ZERO

                if is_in_range(date_str):
                    daily_stats[date_str]["date"] = date_str
                    daily_stats[date_str]["spot_pnl"] += trade_pnl
                    daily_stats[date_str]["volume_usdt"] += matched_revenue
                    daily_stats[date_str]["fees_usdt"] += prop_fee
                    pnl_pct_val = (
                        ((price - entry_avg_cost) / entry_avg_cost * HUNDRED)
                        if entry_avg_cost > ZERO
                        else ZERO
                    )
                    daily_stats[date_str]["spot_trades"].append({
                        "id": t.get("id"),
                        "order_id": t.get("order_id"),
                        "market": t.get("market"),
                        "symbol": symbol,
                        "type": "sell",
                        "price": _to_float(price),
                        "amount": _to_float(matched_qty),
                        "total": _to_float(matched_revenue),
                        "fee": _to_float(fee_raw * ratio),
                        "pnl": _to_float(trade_pnl, 2),
                        "pnl_pct": _to_float(pnl_pct_val, 1),
                        "timestamp": ts,
                    })

    # 2. Process Futures Past Positions
    for pos in past_positions:
        closed_at = str(pos.get("closed_at", "") or pos.get("opened_at", ""))
        date_str = to_tehran_date_str(closed_at)
        if not is_in_range(date_str):
            continue

        pnl_val = D(
            pos.get("net_pnl")
            if pos.get("net_pnl") not in (None, "")
            else (pos.get("pnl") if pos.get("pnl") not in (None, "") else pos.get("PNL"))
        )
        dst = str(pos.get("dst_currency", pos.get("dstCurrency", ""))).upper()

        pnl_usdt = pnl_val if dst == "USDT" else ZERO
        pnl_irt = (pnl_val / TEN) if dst in ("RLS", "IRR") else (pnl_val if dst == "IRT" else ZERO)

        entry_price = D(pos.get("entry_price"))
        collateral = D(pos.get("collateral"))
        leverage = D(pos.get("leverage"), default="1")
        if leverage <= ZERO:
            leverage = ONE
        pos_vol = _round_dec(collateral * leverage, 2) if dst == "USDT" else ZERO

        daily_stats[date_str]["date"] = date_str
        daily_stats[date_str]["futures_pnl_usdt"] += pnl_usdt
        daily_stats[date_str]["futures_pnl_irt"] += pnl_irt
        daily_stats[date_str]["volume_usdt"] += pos_vol

        daily_stats[date_str]["futures_positions"].append({
            "id": pos.get("id"),
            "market": f"{pos.get('src_currency')}-{pos.get('dst_currency')}",
            "src_currency": pos.get("src_currency"),
            "dst_currency": dst,
            "side": pos.get("side"),
            "leverage": pos.get("leverage"),
            "entry_price": _to_float(entry_price),
            "exit_price": _to_float(D(pos.get("exit_price"))),
            "pnl": _to_float(pnl_val, 2),
            "pnl_usdt": _to_float(pnl_usdt, 2),
            "pnl_irt": int(_round_dec(pnl_irt, 0)),
            "gross_pnl": _to_float(D(pos.get("gross_pnl"))),
            "pool_pnl_share": _to_float(D(pos.get("pool_pnl_share"))),
            "total_fee": _to_float(D(pos.get("total_fee"))),
            "pnl_percent": _to_float(D(pos.get("pnl_percent"))),
            "closed_at": closed_at,
            "status": pos.get("status"),
        })

    # 3. Finalize Daily Totals and Classify Status
    formatted_daily: Dict[str, Dict[str, Any]] = {}
    winning_days = 0
    losing_days = 0
    total_net_pnl = ZERO
    total_trades_count = 0

    for d, item in daily_stats.items():
        if not item["date"]:
            item["date"] = d

        spot_pnl = _round_dec(item["spot_pnl"], 2)
        fut_pnl = _round_dec(item["futures_pnl_usdt"], 2)
        fut_irt = _round_dec(item["futures_pnl_irt"], 0)

        # Convert IRT futures PnL to USDT using live Nobitex USDT-RLS rate if available
        fut_irt_usdt = ZERO
        if fut_irt != ZERO and usdt_rls_rate > ZERO:
            toman_rate = usdt_rls_rate / TEN
            fut_irt_usdt = _round_dec(fut_irt / toman_rate, 2)

        tot_pnl = _round_dec(spot_pnl + fut_pnl + fut_irt_usdt, 2)

        item["spot_pnl"] = float(spot_pnl)
        item["futures_pnl_usdt"] = float(fut_pnl)
        item["futures_pnl_irt"] = int(fut_irt)
        item["total_pnl"] = float(tot_pnl)
        item["volume_usdt"] = _to_float(item["volume_usdt"], 2)
        item["fees_usdt"] = _to_float(item["fees_usdt"], 2)

        total_actions = len(item["spot_trades"]) + len(item["futures_positions"])
        item["trades_count"] = total_actions
        total_trades_count += total_actions

        if tot_pnl > EPSILON_DAY or fut_irt > TEN:
            item["status"] = "profit"
            winning_days += 1
        elif tot_pnl < -EPSILON_DAY or fut_irt < -TEN:
            item["status"] = "loss"
            losing_days += 1
        else:
            item["status"] = "neutral"

        total_net_pnl += tot_pnl
        formatted_daily[d] = item

    active_days_count = len(formatted_daily)
    decided_days = winning_days + losing_days
    win_rate = (
        _to_float((Decimal(winning_days) / Decimal(decided_days)) * HUNDRED, 1)
        if decided_days > 0
        else 0.0
    )

    days_list = list(formatted_daily.values())
    best_day = max(days_list, key=lambda x: x["total_pnl"]) if days_list else None
    worst_day = min(days_list, key=lambda x: x["total_pnl"]) if days_list else None

    return {
        "daily_stats": formatted_daily,
        "summary": {
            "total_realized_pnl_usdt": _to_float(total_net_pnl, 2),
            "total_active_days": active_days_count,
            "winning_days": winning_days,
            "losing_days": losing_days,
            "break_even_days": active_days_count - (winning_days + losing_days),
            "win_rate_pct": win_rate,
            "total_trades_count": total_trades_count,
            "best_day": {
                "date": best_day["date"],
                "pnl": best_day["total_pnl"],
            } if best_day else None,
            "worst_day": {
                "date": worst_day["date"],
                "pnl": worst_day["total_pnl"],
            } if worst_day else None,
        },
    }


def calculate_equity_curve(
    trades: List[Dict[str, Any]],
    past_positions: List[Dict[str, Any]],
    wallets: Optional[List[Dict[str, Any]]] = None,
    market_prices: Optional[Dict[str, Any]] = None,
    btc_prices_map: Optional[Dict[str, float]] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    since_date: Optional[str] = None,
    timeframe: str = "all",  # '7d', '30d', '90d', 'ytd', 'all', 'custom'
    cashflows: Optional[List[Dict[str, Any]]] = None,
    daily_snapshots: Optional[Dict[str, Dict[str, Any]]] = None,
    inventory_cashflows: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Calculate daily continuous equity curve, cumulative PnL, Time-Weighted Return (TWR),
    and Bitcoin benchmark comparison, cleanly separating external deposits/withdrawals
    from trading performance.
    `inventory_cashflows` drives Spot WAC crypto withdrawal replay even when `cashflows=[]`
    (preserving ADR-013 Spot equity isolation).
    """
    if wallets is None:
        wallets = []
    if market_prices is None:
        market_prices = {}
    if btc_prices_map is None:
        btc_prices_map = {}
    if cashflows is None:
        cashflows = []
    if daily_snapshots is None:
        daily_snapshots = {}

    effective_from = from_date or (since_date if since_date and since_date not in ("all", "cycle") else None)
    effective_inventory_cf = inventory_cashflows if inventory_cashflows is not None else cashflows

    # 1. Calculate live portfolio value
    port = calculate_portfolio(
        trades,
        wallets,
        market_prices,
        from_date=effective_from,
        to_date=to_date,
        cashflows=effective_inventory_cf,
    )
    current_portfolio_value = D(port.get("summary", {}).get("total_portfolio_value"))

    # 2. Get daily pnl breakdown (spot sells + futures closed positions)
    daily_cal = calculate_daily_pnl_calendar(
        trades,
        past_positions,
        market_prices,
        from_date=None,
        to_date=None,
        cashflows=effective_inventory_cf,
    )
    daily_stats = daily_cal.get("daily_stats", {})

    # Aggregate daily external cashflows (deposits - withdrawals) in USDT per IRST day
    usdt_rls_rate = _get_usdt_rls_rate(market_prices)
    daily_cashflows: Dict[str, Decimal] = defaultdict(lambda: ZERO)
    daily_deposits: Dict[str, Decimal] = defaultdict(lambda: ZERO)
    daily_withdrawals: Dict[str, Decimal] = defaultdict(lambda: ZERO)
    for cf in cashflows:
        d_str = to_tehran_date_str(str(cf.get("timestamp", "")))
        if not d_str:
            continue
        ftype = str(cf.get("flow_type", "")).strip().lower()
        amt = D(cf.get("amount"))
        curr = str(cf.get("currency", "")).strip().upper()
        usdt_val = _cashflow_amount_to_usdt(amt, curr, market_prices, usdt_rls_rate)
        if usdt_val <= ZERO:
            continue
        if ftype == "deposit":
            daily_cashflows[d_str] += usdt_val
            daily_deposits[d_str] += usdt_val
        elif ftype == "withdraw":
            daily_cashflows[d_str] -= usdt_val
            daily_withdrawals[d_str] += usdt_val

    # Merge any net_cashflow_usdt recorded in daily_snapshots when cashflows table has no entry for that day
    for d_str, snap in daily_snapshots.items():
        if d_str not in daily_cashflows and isinstance(snap, dict):
            snap_cf = D(snap.get("net_cashflow_usdt"))
            if snap_cf != ZERO:
                daily_cashflows[d_str] = snap_cf
                if snap_cf > ZERO:
                    daily_deposits[d_str] += snap_cf
                else:
                    daily_withdrawals[d_str] += abs(snap_cf)

    # 3. Determine start and end date based on timeframe or explicit date range (IRST)
    today = datetime.datetime.now(TEHRAN_TZ).date()
    end_date = today

    if to_date:
        try:
            end_date = datetime.date.fromisoformat(to_date[:10])
        except ValueError:
            end_date = today

    if effective_from:
        try:
            start_date = datetime.date.fromisoformat(effective_from[:10])
        except ValueError:
            start_date = end_date - datetime.timedelta(days=29)
    elif timeframe == "7d":
        start_date = end_date - datetime.timedelta(days=6)
    elif timeframe == "30d":
        start_date = end_date - datetime.timedelta(days=29)
    elif timeframe == "90d":
        start_date = end_date - datetime.timedelta(days=89)
    elif timeframe == "ytd":
        start_date = datetime.date(end_date.year, 1, 1)
    else:  # 'all' or default
        trade_days = sorted(daily_stats.keys())
        all_days = sorted(set(trade_days) | set(daily_cashflows.keys()))
        target_days = trade_days if trade_days else all_days
        if target_days:
            try:
                start_date = datetime.date.fromisoformat(target_days[0])
            except ValueError:
                start_date = end_date - datetime.timedelta(days=365)
        else:
            start_date = end_date - datetime.timedelta(days=365)

    if start_date > end_date:
        start_date = end_date

    # Build continuous day list
    cur = start_date
    period_days: List[str] = []
    while cur <= end_date:
        period_days.append(cur.strftime("%Y-%m-%d"))
        cur += datetime.timedelta(days=1)

    # 4. Calculate initial equity at start of period (backing out both PnL and net external cashflows)
    total_period_pnl = sum(
        (D(daily_stats.get(d, {}).get("total_pnl")) for d in period_days),
        ZERO,
    )
    total_period_cf = sum((daily_cashflows.get(d, ZERO) for d in period_days), ZERO)
    total_deposits_usdt = sum((daily_deposits.get(d, ZERO) for d in period_days), ZERO)
    total_withdrawals_usdt = sum((daily_withdrawals.get(d, ZERO) for d in period_days), ZERO)

    back_calculated = current_portfolio_value - total_period_pnl - total_period_cf
    initial_equity = max(back_calculated, ZERO) if back_calculated > ZERO else current_portfolio_value

    # Determine baseline BTC price at start of period
    first_btc_price_raw = btc_prices_map.get(period_days[0]) if period_days else None
    if not first_btc_price_raw:
        sorted_btc_keys = sorted(btc_prices_map.keys())
        if sorted_btc_keys:
            earlier = [k for k in sorted_btc_keys if k <= period_days[0]]
            first_btc_price = D(btc_prices_map[earlier[-1]] if earlier else btc_prices_map[sorted_btc_keys[0]])
        else:
            first_btc_price = Decimal("80000")
    else:
        first_btc_price = D(first_btc_price_raw)

    # 5. Build timeline with Time-Weighted Return (TWR) so deposits/withdrawals never distort returns
    cum_pnl = ZERO
    running_equity = initial_equity
    twr_factor = ONE
    peak_twr_equity = initial_equity
    max_drawdown_usdt = ZERO
    peak_equity = initial_equity
    timeline: List[Dict[str, Any]] = []
    last_known_btc = first_btc_price

    for d_str in period_days:
        st = daily_stats.get(d_str, {})
        d_pnl = D(st.get("total_pnl"))
        s_pnl = D(st.get("spot_pnl"))
        f_pnl = D(st.get("futures_pnl_usdt"))
        vol = D(st.get("volume_usdt"))
        fees = D(st.get("fees_usdt"))
        trades_cnt = st.get("trades_count", 0)
        d_cf = daily_cashflows.get(d_str, ZERO)

        prev_equity = running_equity
        # Capital base at start of day including external inflows (Modified Dietz / Start-of-Day TWR)
        capital_base = max(prev_equity + (d_cf if d_cf > ZERO else ZERO), ONE)
        daily_ret = (d_pnl / capital_base) if d_pnl != ZERO else ZERO
        twr_factor = twr_factor * (ONE + daily_ret)

        cum_pnl += d_pnl
        running_equity = prev_equity + d_cf + d_pnl
        equity_q = _round_dec(running_equity, 2)
        daily_ret_pct = _round_dec(daily_ret * HUNDRED, 2)
        cum_ret_pct = _round_dec((twr_factor - ONE) * HUNDRED, 2)

        if equity_q > peak_equity:
            peak_equity = equity_q

        # Drawdown measured on cashflow-neutral TWR equity index so withdrawals never look like losses
        twr_equity_idx = initial_equity * twr_factor
        if twr_equity_idx > peak_twr_equity:
            peak_twr_equity = twr_equity_idx
        dd_usdt = peak_twr_equity - twr_equity_idx
        if dd_usdt > max_drawdown_usdt:
            max_drawdown_usdt = dd_usdt

        btc_p_raw = btc_prices_map.get(d_str)
        btc_p = D(btc_p_raw) if btc_p_raw else ZERO
        if btc_p > ZERO:
            last_known_btc = btc_p
        else:
            btc_p = last_known_btc

        btc_ret_pct = (
            _round_dec(((btc_p - first_btc_price) / first_btc_price * HUNDRED), 2)
            if first_btc_price > ZERO
            else ZERO
        )
        alpha_pct = _round_dec(cum_ret_pct - btc_ret_pct, 2)

        timeline.append({
            "date": d_str,
            "daily_pnl": _to_float(d_pnl, 2),
            "spot_pnl": _to_float(s_pnl, 2),
            "futures_pnl": _to_float(f_pnl, 2),
            "net_cashflow_usdt": _to_float(d_cf, 2),
            "cumulative_pnl": _to_float(cum_pnl, 2),
            "portfolio_equity": float(equity_q),
            "daily_return_pct": float(daily_ret_pct),
            "cumulative_return_pct": float(cum_ret_pct),
            "volume_usdt": _to_float(vol, 2),
            "fees_usdt": _to_float(fees, 2),
            "trades_count": trades_cnt,
            "btc_price": _to_float(btc_p, 2),
            "btc_return_pct": float(btc_ret_pct),
            "alpha_pct": float(alpha_pct),
        })

    max_drawdown_pct = (
        _round_dec((max_drawdown_usdt / peak_twr_equity * HUNDRED), 2)
        if peak_twr_equity > ZERO
        else ZERO
    )
    final_equity = D(timeline[-1]["portfolio_equity"]) if timeline else initial_equity
    net_profit = _round_dec(cum_pnl, 2)
    total_return_pct = D(timeline[-1]["cumulative_return_pct"]) if timeline else ZERO
    final_btc_return = D(timeline[-1]["btc_return_pct"]) if timeline else ZERO
    alpha_vs_btc = _round_dec(total_return_pct - final_btc_return, 2)

    winning_days = sum(1 for item in timeline if item["daily_pnl"] > 0.005)
    losing_days = sum(1 for item in timeline if item["daily_pnl"] < -0.005)
    even_days = len(timeline) - (winning_days + losing_days)

    return {
        "timeframe": timeframe,
        "start_date": period_days[0] if period_days else "",
        "end_date": period_days[-1] if period_days else "",
        "timeline": timeline,
        "metrics": {
            "initial_equity": _to_float(initial_equity, 2),
            "current_equity": _to_float(final_equity, 2),
            "net_profit": float(net_profit),
            "total_return_pct": float(total_return_pct),
            "total_net_cashflow_usdt": _to_float(total_period_cf, 2),
            "total_deposits_usdt": _to_float(total_deposits_usdt, 2),
            "total_withdrawals_usdt": _to_float(total_withdrawals_usdt, 2),
            "return_methodology": "TWR",
            "peak_equity": _to_float(peak_equity, 2),
            "max_drawdown_usdt": _to_float(max_drawdown_usdt, 2),
            "max_drawdown_pct": float(max_drawdown_pct),
            "first_btc_price": _to_float(first_btc_price, 2),
            "latest_btc_price": _to_float(last_known_btc, 2),
            "btc_total_return_pct": float(final_btc_return),
            "alpha_vs_btc": float(alpha_vs_btc),
            "winning_days": winning_days,
            "losing_days": losing_days,
            "even_days": even_days,
            "total_days": len(timeline),
        },
    }
