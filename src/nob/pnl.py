import datetime
import zoneinfo
from typing import List, Dict, Any, Optional
from collections import defaultdict

TEHRAN_TZ = zoneinfo.ZoneInfo("Asia/Tehran")


def to_tehran_date_str(timestamp_str: str) -> str:
    """Convert UTC ISO timestamp to YYYY-MM-DD in Asia/Tehran timezone.
    Correctly accounts for Iran Standard Time (UTC+03:30, no DST changes since September 2022).
    """
    if not timestamp_str or len(timestamp_str) < 10:
        return ""
    try:
        clean_ts = timestamp_str.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(clean_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        dt_tehran = dt.astimezone(TEHRAN_TZ)
        return dt_tehran.strftime("%Y-%m-%d")
    except Exception:
        return timestamp_str[:10]


def calculate_portfolio(
    trades: List[Dict[str, Any]],
    wallets: Optional[List[Dict[str, Any]]] = None,
    market_stats: Optional[Dict[str, Any]] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    since_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Calculate spot portfolio metrics in USDT using Weighted Average Cost (WAC).
    
    Key Improvements:
    - Zero-Cost Sell Protection: Unmatched sells (without prior buys in history)
      are NEVER treated as pure profit. Only inventory-covered sells realize PnL.
    - Fee Accounting:
      * BUY fees: converted from crypto to USDT using (fee * buy_price).
      * SELL fees: subtracted directly from sell revenue in USDT.
    - Date filtering: optional from_date and to_date range filtering.
    """
    if wallets is None:
        wallets = []
    if market_stats is None:
        market_stats = {}

    # Support legacy since_date as alias for from_date
    effective_from = from_date or (since_date if since_date and since_date != "all" else None)

    # Filter trades by date if requested
    if effective_from:
        trades = [t for t in trades if str(t.get("timestamp", "")) >= effective_from]
    if to_date:
        to_cutoff = to_date if len(to_date) > 10 else f"{to_date} 23:59:59"
        trades = [t for t in trades if str(t.get("timestamp", "")) <= to_cutoff]

    # Index wallets by uppercase currency
    wallet_map: Dict[str, Dict[str, Any]] = {}
    for w in wallets:
        curr = str(w.get("currency", "")).upper()
        if curr:
            wallet_map[curr] = w

    # Group trades by base crypto symbol (markets ending with -USDT)
    trades_by_symbol = defaultdict(list)
    for t in trades:
        market = str(t.get("market", "")).upper()
        if "-" in market:
            base, quote = market.split("-", 1)
            if quote == "USDT":
                trades_by_symbol[base].append(t)

    # All unique symbols to evaluate
    all_symbols = set(trades_by_symbol.keys())
    for curr, w in wallet_map.items():
        if curr not in {"RLS", "USDT", "DAI", "IRR", "IRT"}:
            try:
                if float(w.get("balance", 0.0)) > 0:
                    all_symbols.add(curr)
            except (ValueError, TypeError):
                pass

    holdings: Dict[str, Dict[str, Any]] = {}
    total_realized_pnl = 0.0
    total_unrealized_pnl = 0.0
    total_fees_usdt = 0.0
    total_crypto_value = 0.0
    total_invested_capital = 0.0

    for symbol in sorted(all_symbols):
        symbol_trades = trades_by_symbol.get(symbol, [])
        symbol_trades.sort(key=lambda x: str(x.get("timestamp", "")))

        current_qty = 0.0
        avg_cost = 0.0
        realized_pnl = 0.0
        asset_fees_usdt = 0.0
        total_bought_qty = 0.0
        total_bought_cost = 0.0
        total_sold_qty = 0.0
        total_sold_revenue = 0.0

        for t in symbol_trades:
            trade_type = str(t.get("type", "")).lower()
            try:
                price = float(t.get("price", 0.0))
                amount = float(t.get("amount", 0.0))
                total = float(t.get("total", 0.0))
                fee_raw = float(t.get("fee", 0.0))
            except (ValueError, TypeError):
                continue

            if trade_type == "buy":
                # Buy fee is charged in the cryptocurrency unit! Convert to USDT.
                fee_usdt = fee_raw * price
                asset_fees_usdt += fee_usdt

                total_bought_qty += amount
                total_bought_cost += total

                # Net crypto received in wallet after Nobitex fee deduction
                net_amount = max(0.0, amount - fee_raw)

                # Update Weighted Average Cost (WAC) including acquisition fee
                if current_qty + net_amount > 0:
                    avg_cost = ((current_qty * avg_cost) + total) / (current_qty + net_amount)
                current_qty += net_amount

            elif trade_type == "sell":
                # Sell fee is charged in USDT!
                fee_usdt = fee_raw
                asset_fees_usdt += fee_usdt

                total_sold_qty += amount
                total_sold_revenue += total

                # Zero-Cost Sell Protection:
                # Match sell only against existing inventory from known buys
                matched_qty = min(current_qty, amount)
                if matched_qty > 0 and avg_cost > 0:
                    matched_cost = matched_qty * avg_cost
                    matched_revenue = matched_qty * price
                    # Proportionate fee for matched quantity
                    prop_fee = fee_usdt * (matched_qty / amount) if amount > 0 else 0.0
                    trade_pnl = matched_revenue - matched_cost - prop_fee
                    realized_pnl += trade_pnl
                    current_qty -= matched_qty
                    if current_qty <= 1e-9:
                        current_qty = 0.0
                        avg_cost = 0.0
                else:
                    # Unmatched sell (prior history deleted by Nobitex or margin short opening)
                    # DO NOT treat as 100% profit! Ignored from realized PnL.
                    pass

        # Check actual wallet balance if available
        wallet_entry = wallet_map.get(symbol)
        if wallet_entry is not None:
            try:
                wallet_balance = float(wallet_entry.get("balance", 0.0))
                active_balance = float(wallet_entry.get("activeBalance", wallet_balance))
            except (ValueError, TypeError):
                wallet_balance = current_qty
                active_balance = current_qty
        elif wallet_map:
            # Wallets are populated, but asset is not in user's Nobitex wallet -> 0 balance
            wallet_balance = 0.0
            active_balance = 0.0
        else:
            # Completely offline with no wallet snapshot -> fallback to trade ledger qty
            wallet_balance = current_qty
            active_balance = current_qty

        # Effective holding quantity for current valuation
        holding_qty = wallet_balance

        # Look up current market price in USDT
        market_key = f"{symbol}-usdt".lower()
        stat = market_stats.get(market_key, {})
        try:
            current_price = float(stat.get("latest", 0.0))
            day_change = float(stat.get("dayChange", 0.0))
            day_high = float(stat.get("dayHigh", 0.0))
            day_low = float(stat.get("dayLow", 0.0))
        except (ValueError, TypeError):
            current_price = 0.0
            day_change = 0.0
            day_high = 0.0
            day_low = 0.0

        current_value = holding_qty * current_price

        # Unrealized PnL (USDT)
        if holding_qty > 0 and avg_cost > 0 and current_price > 0:
            unrealized_pnl = (current_price - avg_cost) * holding_qty
            pnl_pct = ((current_price - avg_cost) / avg_cost) * 100.0
        else:
            unrealized_pnl = 0.0
            pnl_pct = 0.0

        asset_total_pnl = realized_pnl + unrealized_pnl

        # Accumulate totals
        total_realized_pnl += realized_pnl
        total_unrealized_pnl += unrealized_pnl
        total_fees_usdt += asset_fees_usdt
        total_crypto_value += current_value
        if holding_qty > 0 and avg_cost > 0:
            total_invested_capital += (holding_qty * avg_cost)

        holdings[symbol] = {
            "symbol": symbol,
            "market": f"{symbol}-USDT",
            "quantity": holding_qty,
            "active_quantity": active_balance,
            "avg_cost": avg_cost,
            "current_price": current_price,
            "current_value": current_value,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "total_pnl": asset_total_pnl,
            "pnl_pct": pnl_pct,
            "day_change": day_change,
            "day_high": day_high,
            "day_low": day_low,
            "fees_paid_usdt": asset_fees_usdt,
            "trades_count": len(symbol_trades),
            "total_bought_qty": total_bought_qty,
            "total_bought_cost": total_bought_cost,
            "total_sold_qty": total_sold_qty,
            "total_sold_revenue": total_sold_revenue,
            "is_dust": current_value < 1.0,
        }

    # USDT cash reserves
    usdt_wallet = wallet_map.get("USDT", {})
    try:
        usdt_cash = float(usdt_wallet.get("balance", 0.0))
    except (ValueError, TypeError):
        usdt_cash = 0.0

    dai_wallet = wallet_map.get("DAI", {})
    try:
        dai_cash = float(dai_wallet.get("balance", 0.0))
    except (ValueError, TypeError):
        dai_cash = 0.0

    total_portfolio_value = total_crypto_value + usdt_cash + dai_cash
    total_pnl = total_realized_pnl + total_unrealized_pnl

    overall_pnl_pct = (
        (total_pnl / total_invested_capital * 100.0)
        if total_invested_capital > 0
        else 0.0
    )

    # Assets with meaningful holding value (>= $1.00) vs dust holdings (< $1.00)
    active_holdings = [h for h in holdings.values() if h["current_value"] >= 1.0]
    dust_holdings = [h for h in holdings.values() if h["current_value"] < 1.0]

    # Best and worst performers must be from active holdings (>= $1.00)
    best_performer = max(active_holdings, key=lambda x: x["pnl_pct"]) if active_holdings else None
    worst_performer = min(active_holdings, key=lambda x: x["pnl_pct"]) if active_holdings else None

    # Chart data: Asset allocation (USDT + top cryptos)
    allocation_items = []
    if usdt_cash > 0.01:
        allocation_items.append({
            "symbol": "USDT",
            "value": round(usdt_cash, 2),
            "color": "#10b981",
        })

    sorted_cryptos = sorted(
        [h for h in holdings.values() if h["current_value"] > 0.01],
        key=lambda x: x["current_value"],
        reverse=True,
    )

    top_cryptos = sorted_cryptos[:8]
    other_value = sum(h["current_value"] for h in sorted_cryptos[8:])
    for c in top_cryptos:
        allocation_items.append({
            "symbol": c["symbol"],
            "value": round(c["current_value"], 2),
        })
    if other_value > 0.01:
        allocation_items.append({
            "symbol": "OTHERS",
            "value": round(other_value, 2),
        })

    for item in allocation_items:
        item["percentage"] = (
            round((item["value"] / total_portfolio_value) * 100.0, 1)
            if total_portfolio_value > 0
            else 0.0
        )

    # Chart data: PnL by asset (top 10 by absolute PnL)
    pnl_by_asset = sorted(
        [h for h in holdings.values() if abs(h["total_pnl"]) > 0.01],
        key=lambda x: abs(x["total_pnl"]),
        reverse=True,
    )[:10]

    # Chart data: Monthly trade activity
    trades_timeline = defaultdict(lambda: {"buys": 0, "sells": 0, "volume": 0.0})
    for t in trades:
        ts = str(t.get("timestamp", ""))
        d_str = to_tehran_date_str(ts)
        if len(d_str) >= 7:
            month_key = d_str[:7]
            ttype = str(t.get("type", "")).lower()
            try:
                tot = float(t.get("total", 0.0))
            except (ValueError, TypeError):
                tot = 0.0
            if ttype == "buy":
                trades_timeline[month_key]["buys"] += 1
            else:
                trades_timeline[month_key]["sells"] += 1
            trades_timeline[month_key]["volume"] += tot

    timeline_sorted = [
        {"period": k, **v} for k, v in sorted(trades_timeline.items())
    ]

    return {
        "summary": {
            "total_portfolio_value": round(total_portfolio_value, 2),
            "total_crypto_value": round(total_crypto_value, 2),
            "usdt_cash": round(usdt_cash, 2),
            "dai_cash": round(dai_cash, 2),
            "total_invested_capital": round(total_invested_capital, 2),
            "total_realized_pnl": round(total_realized_pnl, 2),
            "total_unrealized_pnl": round(total_unrealized_pnl, 2),
            "total_pnl": round(total_pnl, 2),
            "overall_pnl_pct": round(overall_pnl_pct, 2),
            "total_fees_usdt": round(total_fees_usdt, 2),
            "active_assets_count": len(active_holdings),
            "dust_assets_count": len(dust_holdings),
            "total_assets_count": len(holdings),
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
                    "realized": round(h["realized_pnl"], 2),
                    "unrealized": round(h["unrealized_pnl"], 2),
                    "total": round(h["total_pnl"], 2),
                    "pnl_pct": round(h["pnl_pct"], 2),
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
) -> Dict[str, Any]:
    """
    Summarize leveraged positions (معاملات تعهدی) from Nobitex /positions/list.
    Includes comprehensive fee accounting: opening fee, closing fee, and auto-renewal (extension) fee.
    """
    if market_stats is None:
        market_stats = {}

    if trades is None:
        try:
            from nob.db import get_margin_trades
            trades = get_margin_trades()
        except Exception:
            trades = []

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

    total_realized_pnl_usdt = 0.0
    total_realized_pnl_rls = 0.0
    wins = 0
    losses = 0

    total_fees_usdt = 0.0
    total_open_fees_usdt = 0.0
    total_close_fees_usdt = 0.0
    total_extension_fees_usdt = 0.0

    total_fees_rls = 0.0
    total_open_fees_rls = 0.0
    total_close_fees_rls = 0.0
    total_extension_fees_rls = 0.0

    for p in positions:
        status = str(p.get("status", "")).strip()
        dst = str(p.get("dst_currency", p.get("dstCurrency", ""))).lower()
        pnl = float(p.get("pnl", p.get("PNL", 0.0)) or 0.0)
        collat = float(p.get("collateral", 0.0) or 0.0)
        lev = float(p.get("leverage", 1.0) or 1.0)
        entry_p = float(p.get("entry_price", 0.0) or 0.0)
        exit_p = float(p.get("exit_price", 0.0) or 0.0)
        rate = 0.00125 if dst == "rls" else 0.0008

        # --- 1. Opening Fee ---
        opened_at = p.get("opened_at")
        open_fee = 0.0
        if opened_at in trades_by_ts:
            oid = trades_by_ts[opened_at].get("order_id")
            fills = trades_by_order.get(oid, [trades_by_ts[opened_at]])
            for fill in fills:
                f_price = float(fill.get("price", 0.0) or 0.0)
                f_fee = float(fill.get("fee", 0.0) or 0.0)
                if fill.get("type") == "buy":
                    open_fee += f_fee * f_price
                else:
                    open_fee += f_fee
        else:
            open_fee = collat * lev * rate

        # --- 2. Closing Fee ---
        closed_at = p.get("closed_at")
        close_fee = 0.0
        if status == "Open" or not closed_at:
            close_fee = 0.0
        elif closed_at in trades_by_ts:
            oid = trades_by_ts[closed_at].get("order_id")
            fills = trades_by_order.get(oid, [trades_by_ts[closed_at]])
            for fill in fills:
                f_price = float(fill.get("price", 0.0) or 0.0)
                f_fee = float(fill.get("fee", 0.0) or 0.0)
                if fill.get("type") == "buy":
                    close_fee += f_fee * f_price
                else:
                    close_fee += f_fee
        else:
            val_exit = (
                collat * lev * (exit_p / entry_p)
                if (entry_p > 0 and exit_p > 0)
                else (collat * lev)
            )
            close_fee = val_exit * rate

        # --- 3. Extension / Auto-Renewal Fee ---
        ext_fee = float(p.get("extension_fee") or p.get("extensionFee") or 0.0)
        if ext_fee <= 0.0 and closed_at and opened_at:
            try:
                t_o = datetime.datetime.fromisoformat(opened_at)
                t_c = datetime.datetime.fromisoformat(closed_at)
                dur_hours = (t_c - t_o).total_seconds() / 3600.0
                periods = int(dur_hours // 8)
                liability = collat * max(0.0, lev - 1.0)
                ext_fee = periods * 0.00033333 * liability
            except Exception:
                ext_fee = 0.0

        # Round individual position fee figures
        if dst == "usdt":
            p["open_fee"] = round(open_fee, 4)
            p["close_fee"] = round(close_fee, 4)
            p["extension_fee"] = round(ext_fee, 4)
            p["total_fee"] = round(open_fee + close_fee + ext_fee, 4)

            total_fees_usdt += p["total_fee"]
            total_open_fees_usdt += p["open_fee"]
            total_close_fees_usdt += p["close_fee"]
            total_extension_fees_usdt += p["extension_fee"]
        else:
            p["open_fee"] = round(open_fee)
            p["close_fee"] = round(close_fee)
            p["extension_fee"] = round(ext_fee)
            p["total_fee"] = round(open_fee + close_fee + ext_fee)

            total_fees_rls += p["total_fee"]
            total_open_fees_rls += p["open_fee"]
            total_close_fees_rls += p["close_fee"]
            total_extension_fees_rls += p["extension_fee"]

        # Calculate TRUE NET PnL after deducting all fees and profit-share
        gross_pnl = float(p.get("pnl", 0.0) or p.get("PNL", 0.0) or 0.0)
        pool_share = float(p.get("pool_pnl_share", 0.0) or p.get("poolPNLShare", 0.0) or 0.0)
        total_fee = float(p["total_fee"])

        if status == "Open":
            net_pnl = gross_pnl - total_fee
        else:
            net_pnl = gross_pnl - pool_share - total_fee

        net_pnl_percent = (net_pnl / collat * 100.0) if collat > 0 else 0.0

        if dst == "usdt":
            p["gross_pnl"] = round(gross_pnl, 4)
            p["pool_pnl_share"] = round(pool_share, 4)
            p["net_pnl"] = round(net_pnl, 4)
            p["pnl"] = round(net_pnl, 4)
        else:
            p["gross_pnl"] = round(gross_pnl)
            p["pool_pnl_share"] = round(pool_share)
            p["net_pnl"] = round(net_pnl)
            p["pnl"] = round(net_pnl)

        p["gross_pnl_percent"] = float(p.get("pnl_percent", 0.0) or 0.0)
        p["net_pnl_percent"] = round(net_pnl_percent, 2)
        p["pnl_percent"] = round(net_pnl_percent, 2)

        if status == "Open":
            # Calculate Liquidation Risk Metrics
            mark_p = float(p.get("mark_price", 0.0) or 0.0)
            entry_p = float(p.get("entry_price", 0.0) or 0.0)
            current_p = mark_p if mark_p > 0 else entry_p
            liq_p = float(p.get("liquidation_price", 0.0) or 0.0)

            if current_p > 0 and liq_p > 0:
                dist_pct = round(abs(current_p - liq_p) / current_p * 100.0, 2)
                dist_amt = round(abs(current_p - liq_p), 2)
                if dist_pct >= 20.0:
                    r_level = "safe"
                elif dist_pct >= 10.0:
                    r_level = "warning"
                else:
                    r_level = "danger"
            else:
                dist_pct = 0.0
                dist_amt = 0.0
                r_level = "unknown"

            p["liquidation_distance_pct"] = dist_pct
            p["liquidation_distance_amount"] = dist_amt
            p["risk_level"] = r_level
            active_positions.append(p)
        else:
            past_positions.append(p)
            if dst == "usdt":
                total_realized_pnl_usdt += p["pnl"]
            elif dst == "rls":
                total_realized_pnl_rls += p["pnl"]

            if p["pnl"] > 0.001:
                wins += 1
            elif p["pnl"] < -0.001:
                losses += 1

    total_past = len(past_positions)
    win_rate_pct = round((wins / total_past * 100.0), 1) if total_past > 0 else 0.0

    usdt_past = [
        p for p in past_positions 
        if str(p.get("dst_currency", p.get("dstCurrency", ""))).lower() == "usdt"
    ]
    best_position = (
        max(usdt_past, key=lambda x: float(x.get("pnl", 0.0) or 0.0))
        if usdt_past
        else None
    )
    worst_position = (
        min(usdt_past, key=lambda x: float(x.get("pnl", 0.0) or 0.0))
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
            "total_realized_pnl_usdt": round(total_realized_pnl_usdt, 2),
            "total_realized_pnl_rls": round(total_realized_pnl_rls, 0),
            "total_realized_pnl_irt": round(total_realized_pnl_rls / 10.0),
            "total_fees_usdt": round(total_fees_usdt, 2),
            "total_open_fees_usdt": round(total_open_fees_usdt, 2),
            "total_close_fees_usdt": round(total_close_fees_usdt, 2),
            "total_extension_fees_usdt": round(total_extension_fees_usdt, 2),
            "total_fees_rls": round(total_fees_rls),
            "total_fees_irt": round(total_fees_rls / 10.0),
            "total_open_fees_irt": round(total_open_fees_rls / 10.0),
            "total_close_fees_irt": round(total_close_fees_rls / 10.0),
            "total_extension_fees_irt": round(total_extension_fees_rls / 10.0),
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
) -> Dict[str, Any]:
    """
    Calculate daily realized PnL breakdown across Spot and Futures.
    Maps each trading day to its spot sells, futures closed positions, fees, volume, and net profit.
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
        "spot_pnl": 0.0,
        "futures_pnl_usdt": 0.0,
        "futures_pnl_irt": 0.0,
        "total_pnl": 0.0,
        "volume_usdt": 0.0,
        "fees_usdt": 0.0,
        "trades_count": 0,
        "status": "neutral",
        "spot_trades": [],
        "futures_positions": [],
    })

    # 1. Process Spot Trades Chronologically per Asset for WAC Realized PnL
    by_symbol: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in trades:
        market = str(t.get("market", "")).upper()
        symbol = market.split("-")[0] if "-" in market else market
        by_symbol[symbol].append(t)

    for symbol, symbol_trades in by_symbol.items():
        current_qty = 0.0
        avg_cost = 0.0

        for t in sorted(symbol_trades, key=lambda x: str(x.get("timestamp", ""))):
            ttype = str(t.get("type", "")).lower()
            try:
                price = float(t.get("price", 0.0))
                amount = float(t.get("amount", 0.0))
                total = float(t.get("total", 0.0))
                fee_raw = float(t.get("fee", 0.0))
            except (ValueError, TypeError):
                continue

            ts = str(t.get("timestamp", ""))
            date_str = to_tehran_date_str(ts)

            if ttype == "buy":
                fee_usdt = fee_raw * price
                net_amount = max(0.0, amount - fee_raw)
                if current_qty + net_amount > 0:
                    avg_cost = ((current_qty * avg_cost) + total) / (current_qty + net_amount)
                current_qty += net_amount

                # Record buy volume and fee if within date range
                if is_in_range(date_str):
                    daily_stats[date_str]["date"] = date_str
                    daily_stats[date_str]["volume_usdt"] += total
                    daily_stats[date_str]["fees_usdt"] += fee_usdt

            elif ttype == "sell":
                fee_usdt = fee_raw
                matched_qty = min(current_qty, amount)

                if matched_qty > 0 and avg_cost > 0:
                    matched_cost = matched_qty * avg_cost
                    matched_revenue = matched_qty * price
                    prop_fee = fee_usdt * (matched_qty / amount) if amount > 0 else 0.0
                    trade_pnl = matched_revenue - matched_cost - prop_fee

                    current_qty -= matched_qty
                    if current_qty <= 1e-9:
                        current_qty = 0.0
                        avg_cost = 0.0

                    if is_in_range(date_str):
                        daily_stats[date_str]["date"] = date_str
                        daily_stats[date_str]["spot_pnl"] += trade_pnl
                        daily_stats[date_str]["volume_usdt"] += total
                        daily_stats[date_str]["fees_usdt"] += fee_usdt
                        daily_stats[date_str]["spot_trades"].append({
                            "id": t.get("id"),
                            "order_id": t.get("order_id"),
                            "market": t.get("market"),
                            "symbol": symbol,
                            "type": "sell",
                            "price": price,
                            "amount": amount,
                            "total": total,
                            "fee": fee_raw,
                            "pnl": round(trade_pnl, 2),
                            "pnl_pct": round(((price - avg_cost) / avg_cost * 100.0), 1) if avg_cost > 0 else 0.0,
                            "timestamp": ts,
                        })
                else:
                    # Unmatched sell
                    if is_in_range(date_str):
                        daily_stats[date_str]["date"] = date_str
                        daily_stats[date_str]["volume_usdt"] += total
                        daily_stats[date_str]["fees_usdt"] += fee_usdt

    # 2. Process Futures Past Positions
    for pos in past_positions:
        closed_at = str(pos.get("closed_at", "") or pos.get("opened_at", ""))
        date_str = to_tehran_date_str(closed_at)
        if not is_in_range(date_str):
            continue

        pnl = float(pos.get("net_pnl", pos.get("pnl", pos.get("PNL", 0.0))) or 0.0)
        dst = str(pos.get("dst_currency", pos.get("dstCurrency", ""))).upper()

        pnl_usdt = pnl if dst == "USDT" else 0.0
        pnl_irt = pnl / 10.0 if dst in ("RLS", "IRR") else (pnl if dst == "IRT" else 0.0)

        entry_price = float(pos.get("entry_price", 0.0) or 0.0)
        collateral = float(pos.get("collateral", 0.0) or 0.0)
        pos_vol = round(entry_price * collateral, 2) if dst == "USDT" else 0.0

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
            "entry_price": entry_price,
            "exit_price": float(pos.get("exit_price", 0.0) or 0.0),
            "pnl": round(pnl, 2),
            "pnl_usdt": round(pnl_usdt, 2),
            "pnl_irt": round(pnl_irt),
            "gross_pnl": float(pos.get("gross_pnl", 0.0)),
            "pool_pnl_share": float(pos.get("pool_pnl_share", 0.0)),
            "total_fee": float(pos.get("total_fee", 0.0)),
            "pnl_percent": float(pos.get("pnl_percent", 0.0) or 0.0),
            "closed_at": closed_at,
            "status": pos.get("status"),
        })

    # 3. Finalize Daily Totals and Classify Status
    formatted_daily: Dict[str, Dict[str, Any]] = {}
    winning_days = 0
    losing_days = 0
    total_net_pnl = 0.0
    total_trades_count = 0

    for d, item in daily_stats.items():
        if not item["date"]:
            item["date"] = d

        spot_pnl = round(item["spot_pnl"], 2)
        fut_pnl = round(item["futures_pnl_usdt"], 2)
        fut_irt = round(item["futures_pnl_irt"])

        # Convert IRT futures PnL to USDT using live Nobitex USDT-RLS rate if available
        fut_irt_usdt = 0.0
        if fut_irt != 0 and market_stats:
            rls_rate = float(market_stats.get("usdt-rls", {}).get("latest", 0.0) or 0.0)
            if rls_rate > 0:
                toman_rate = rls_rate / 10.0
                fut_irt_usdt = round(fut_irt / toman_rate, 2)

        tot_pnl = round(spot_pnl + fut_pnl + fut_irt_usdt, 2)

        item["spot_pnl"] = spot_pnl
        item["futures_pnl_usdt"] = fut_pnl
        item["futures_pnl_irt"] = fut_irt
        item["total_pnl"] = tot_pnl
        item["volume_usdt"] = round(item["volume_usdt"], 2)
        item["fees_usdt"] = round(item["fees_usdt"], 2)

        total_actions = len(item["spot_trades"]) + len(item["futures_positions"])
        item["trades_count"] = total_actions
        total_trades_count += total_actions

        if tot_pnl > 0.005 or fut_irt > 10:
            item["status"] = "profit"
            winning_days += 1
        elif tot_pnl < -0.005 or fut_irt < -10:
            item["status"] = "loss"
            losing_days += 1
        else:
            item["status"] = "neutral"

        total_net_pnl += tot_pnl
        formatted_daily[d] = item

    active_days_count = len(formatted_daily)
    win_rate = round((winning_days / active_days_count * 100.0), 1) if active_days_count > 0 else 0.0

    days_list = list(formatted_daily.values())
    best_day = max(days_list, key=lambda x: x["total_pnl"]) if days_list else None
    worst_day = min(days_list, key=lambda x: x["total_pnl"]) if days_list else None

    return {
        "daily_stats": formatted_daily,
        "summary": {
            "total_realized_pnl_usdt": round(total_net_pnl, 2),
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
        }
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
) -> Dict[str, Any]:
    """
    Calculate daily continuous equity curve, cumulative PnL, and Bitcoin benchmark comparison.
    Fills all calendar days without gaps so the timeline is continuous.
    """
    if wallets is None:
        wallets = []
    if market_prices is None:
        market_prices = {}
    if btc_prices_map is None:
        btc_prices_map = {}

    effective_from = from_date or (since_date if since_date and since_date not in ("all", "cycle") else None)

    # 1. Calculate live portfolio value
    port = calculate_portfolio(trades, wallets, market_prices, from_date=effective_from, to_date=to_date)
    current_portfolio_value = port.get("summary", {}).get("total_portfolio_value", 0.0)

    # 2. Get daily pnl breakdown (spot sells + futures closed positions)
    daily_cal = calculate_daily_pnl_calendar(trades, past_positions, market_prices, from_date=None, to_date=None)
    daily_stats = daily_cal.get("daily_stats", {})

    # 3. Determine start and end date based on timeframe or explicit date range (Asia/Tehran date)
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
        all_days = sorted(daily_stats.keys())
        if all_days:
            try:
                start_date = datetime.date.fromisoformat(all_days[0])
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

    # 4. Calculate initial equity at start of period
    total_period_pnl = sum(
        daily_stats.get(d, {}).get("total_pnl", 0.0) for d in period_days
    )
    initial_equity = max(current_portfolio_value - total_period_pnl, 100.0)

    # Determine baseline BTC price at start of period
    first_btc_price = btc_prices_map.get(period_days[0]) if period_days else None
    if not first_btc_price:
        sorted_btc_keys = sorted(btc_prices_map.keys())
        if sorted_btc_keys:
            earlier = [k for k in sorted_btc_keys if k <= period_days[0]]
            first_btc_price = btc_prices_map[earlier[-1]] if earlier else btc_prices_map[sorted_btc_keys[0]]
        else:
            first_btc_price = 80000.0

    # 5. Build timeline
    cum_pnl = 0.0
    timeline: List[Dict[str, Any]] = []
    last_known_btc = first_btc_price
    peak_equity = initial_equity
    max_drawdown_usdt = 0.0

    for d_str in period_days:
        st = daily_stats.get(d_str, {})
        d_pnl = st.get("total_pnl", 0.0)
        s_pnl = st.get("spot_pnl", 0.0)
        f_pnl = st.get("futures_pnl_usdt", 0.0)
        vol = st.get("volume_usdt", 0.0)
        fees = st.get("fees_usdt", 0.0)
        trades_cnt = st.get("trades_count", 0)

        cum_pnl += d_pnl
        equity = round(initial_equity + cum_pnl, 2)
        cum_ret_pct = round((cum_pnl / initial_equity * 100.0), 2) if initial_equity > 0 else 0.0

        # Drawdown tracking
        if equity > peak_equity:
            peak_equity = equity
        dd_usdt = peak_equity - equity
        if dd_usdt > max_drawdown_usdt:
            max_drawdown_usdt = dd_usdt

        # BTC tracking
        btc_p = btc_prices_map.get(d_str)
        if btc_p and btc_p > 0:
            last_known_btc = btc_p
        else:
            btc_p = last_known_btc

        btc_ret_pct = round(((btc_p - first_btc_price) / first_btc_price * 100.0), 2) if first_btc_price > 0 else 0.0
        alpha_pct = round(cum_ret_pct - btc_ret_pct, 2)

        timeline.append({
            "date": d_str,
            "daily_pnl": round(d_pnl, 2),
            "spot_pnl": round(s_pnl, 2),
            "futures_pnl": round(f_pnl, 2),
            "cumulative_pnl": round(cum_pnl, 2),
            "portfolio_equity": equity,
            "cumulative_return_pct": cum_ret_pct,
            "volume_usdt": round(vol, 2),
            "fees_usdt": round(fees, 2),
            "trades_count": trades_cnt,
            "btc_price": round(btc_p, 2),
            "btc_return_pct": btc_ret_pct,
            "alpha_pct": alpha_pct,
        })

    max_drawdown_pct = round((max_drawdown_usdt / peak_equity * 100.0), 2) if peak_equity > 0 else 0.0
    final_equity = timeline[-1]["portfolio_equity"] if timeline else initial_equity
    net_profit = round(cum_pnl, 2)
    total_return_pct = timeline[-1]["cumulative_return_pct"] if timeline else 0.0
    final_btc_return = timeline[-1]["btc_return_pct"] if timeline else 0.0
    alpha_vs_btc = round(total_return_pct - final_btc_return, 2)

    winning_days = sum(1 for item in timeline if item["daily_pnl"] > 0.005)
    losing_days = sum(1 for item in timeline if item["daily_pnl"] < -0.005)
    even_days = len(timeline) - (winning_days + losing_days)

    return {
        "timeframe": timeframe,
        "start_date": period_days[0] if period_days else "",
        "end_date": period_days[-1] if period_days else "",
        "timeline": timeline,
        "metrics": {
            "initial_equity": round(initial_equity, 2),
            "current_equity": round(final_equity, 2),
            "net_profit": net_profit,
            "total_return_pct": total_return_pct,
            "peak_equity": round(peak_equity, 2),
            "max_drawdown_usdt": round(max_drawdown_usdt, 2),
            "max_drawdown_pct": max_drawdown_pct,
            "first_btc_price": round(first_btc_price, 2),
            "latest_btc_price": round(last_known_btc, 2),
            "btc_total_return_pct": final_btc_return,
            "alpha_vs_btc": alpha_vs_btc,
            "winning_days": winning_days,
            "losing_days": losing_days,
            "even_days": even_days,
            "total_days": len(timeline),
        }
    }

