import io
import csv
import time
import threading
import json
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timezone, timedelta
from collections import defaultdict, OrderedDict

from nob.time_utils import IRST, now_irst, today_irst_str, ts_to_irst_date_str

TEHRAN_TZ = IRST

import uvicorn
from fastapi import FastAPI, HTTPException, BackgroundTasks, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

try:
    import jdatetime
except ImportError:
    jdatetime = None

from nob.db import (
    init_db,
    upsert_trades,
    get_trades,
    get_stats,
    get_distinct_markets,
    upsert_wallet_snapshots,
    get_wallet_snapshots,
    upsert_market_prices,
    get_market_prices,
    get_all_crypto_trades_chronological,
    upsert_positions,
    get_positions,
    upsert_orders,
    upsert_btc_daily_history,
    get_btc_daily_history,
    upsert_journal_entry,
    get_journal_entry,
    get_all_journal_entries,
    delete_journal_entry,
    get_journal_tags_summary,
    get_chart_state,
    save_chart_state,
    get_open_orders,
    reconcile_open_orders,
    update_order_status,
    get_max_trade_id,
    get_max_order_id,
    get_known_closed_position_ids,
    get_active_position_ids,
    record_last_sync,
    set_metadata_json,
    get_metadata_cache,
    get_metadata_json,
    upsert_cashflows,
    get_cashflows,
    upsert_daily_equity_snapshots,
    get_daily_equity_snapshots,
)
from nob.nobitex_client import (
    NobitexClient,
    NobitexAPIError,
    NobitexAuthError,
    NobitexRateLimitError,
)
from nob.pnl import (
    calculate_portfolio,
    calculate_positions_summary,
    calculate_daily_pnl_calendar,
    calculate_equity_curve,
)
from nob.ws_bridge import NobitexWSBridge

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"

_nobitex_client: Optional[NobitexClient] = None
_client_lock = threading.Lock()
_ws_bridge: Optional[NobitexWSBridge] = None


def get_nobitex_client() -> NobitexClient:
    """Return shared singleton NobitexClient (lazy-initialized, public-safe)."""
    global _nobitex_client
    if _nobitex_client is None:
        with _client_lock:
            if _nobitex_client is None:
                _nobitex_client = NobitexClient(require_auth=False)
    return _nobitex_client


def _invalidate_open_orders_cache() -> None:
    global _open_orders_cache
    _open_orders_cache = {"timestamp": 0.0, "data": None}


def get_ws_bridge() -> NobitexWSBridge:
    """Return shared singleton NobitexWSBridge."""
    global _ws_bridge
    if _ws_bridge is None:
        _ws_bridge = NobitexWSBridge(
            client_factory=get_nobitex_client,
            on_order_update=_invalidate_open_orders_cache,
        )
    return _ws_bridge


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    get_nobitex_client()
    bridge = get_ws_bridge()
    await bridge.start()
    yield
    await bridge.stop()
    global _nobitex_client, _ws_bridge
    _ws_bridge = None
    if _nobitex_client is not None:
        _nobitex_client.close()
        _nobitex_client = None


app = FastAPI(title="Nobitex Portfolio & Trades", version="2.1.0", lifespan=lifespan)

# Global sync status
sync_status = {
    "is_syncing": False,
    "last_result": None,
    "last_error": None,
    "warnings": [],
    "mode": None,
    "step": None,
}


@app.get("/api/stats")
def api_get_stats():
    """Return dashboard KPI stats."""
    return get_stats()


@app.get("/api/markets")
def api_get_markets():
    """Return distinct active markets with live cached leverage & precision metadata."""
    markets = get_distinct_markets()
    prices = get_market_prices()
    margin_markets = get_metadata_json("margin_markets_cache") or {}
    sys_options = get_metadata_json("system_options_cache") or {}
    nob_opts = sys_options.get("nobitex") if isinstance(sys_options.get("nobitex"), dict) else sys_options
    price_precisions = nob_opts.get("pricePrecisions") if isinstance(nob_opts, dict) else {}
    amount_precisions = nob_opts.get("amountPrecisions") if isinstance(nob_opts, dict) else {}
    price_precisions = price_precisions if isinstance(price_precisions, dict) else {}
    amount_precisions = amount_precisions if isinstance(amount_precisions, dict) else {}

    fallback_leverage_map = {
        "BTC": 10,
        "ETH": 10,
        "USDT": 10,
        "XAUT": 10,
        "SOL": 5,
        "DOGE": 5,
        "XRP": 5,
        "ADA": 5,
        "LTC": 5,
        "BNB": 5,
        "BCH": 5,
        "TRX": 5,
        "TON": 5,
        "SUI": 5,
        "HBAR": 5,
        "UNI": 5,
        "AAVE": 5,
    }

    details = {}
    for m in markets:
        parts = m.split("-")
        src = parts[0].upper() if len(parts) > 0 else ""
        dst = parts[1].upper() if len(parts) > 1 else ""
        m_clean = m.lower().replace("-", "")
        price_info = prices.get(m_clean) or prices.get(m.lower())
        latest_price = float(price_info.get("latest", 0.0)) if price_info else 0.0

        # Lookup live maxLeverage & precisions (Nobitex keys Rial markets as *IRT)
        candidate_keys = [f"{src}{dst}"]
        if dst in ("RLS", "IRR"):
            candidate_keys.append(f"{src}IRT")

        live_lev = None
        for k in candidate_keys:
            m_cfg = margin_markets.get(k) if isinstance(margin_markets, dict) else None
            if isinstance(m_cfg, dict) and m_cfg.get("maxLeverage") is not None:
                try:
                    val = float(m_cfg["maxLeverage"])
                    if val > 0:
                        live_lev = int(val) if val.is_integer() else val
                        break
                except (ValueError, TypeError):
                    pass

        max_lev = live_lev if live_lev is not None else fallback_leverage_map.get(src, 5)

        p_prec = None
        a_prec = None
        for k in candidate_keys:
            if p_prec is None and k in price_precisions:
                p_prec = price_precisions[k]
            if a_prec is None and k in amount_precisions:
                a_prec = amount_precisions[k]

        details[m] = {
            "market": m,
            "src_currency": src,
            "dst_currency": dst,
            "latest": latest_price,
            "max_leverage": max_lev,
            "price_precision": p_prec,
            "amount_precision": a_prec,
        }

    return {
        "markets": markets,
        "details": details,
    }


@app.get("/api/portfolio")
def api_get_portfolio(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    since: Optional[str] = None,
):
    """
    Return spot portfolio analytics in USDT using Weighted Average Cost (WAC).
    Optionally filter by from_date and to_date.
    """
    wallets = get_wallet_snapshots()
    prices = get_market_prices()

    # If prices are empty, fetch from Nobitex public API
    if not prices:
        try:
            client = get_nobitex_client()
            fresh_prices = client.fetch_market_stats()
            upsert_market_prices(fresh_prices)
            prices = fresh_prices
        except Exception:
            pass

    effective_from = from_date or (since if since and since != "all" else None)
    # Load full chronological spot history so WAC avg_cost is always replayed accurately
    trades = get_all_crypto_trades_chronological(from_date=None, to_date=None)
    cashflows = get_cashflows(confirmed_only=True)
    portfolio = calculate_portfolio(
        trades,
        wallets,
        prices,
        from_date=effective_from,
        to_date=to_date,
        cashflows=cashflows,
    )
    return portfolio


@app.get("/api/portfolio/charts")
def api_get_portfolio_charts(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    since: Optional[str] = None,
):
    """Return chart data for Asset Allocation, PnL by Asset, and Timeline."""
    portfolio = api_get_portfolio(from_date=from_date, to_date=to_date, since=since)
    return portfolio.get("charts", {})


@app.get("/api/positions")
def api_get_positions():
    """
    Return leveraged/margin positions from /positions/list.
    """
    prices = get_market_prices()
    positions = get_positions()

    # If no positions saved yet, fetch directly from Nobitex (paginated)
    if not positions:
        try:
            client = get_nobitex_client()
            active = client.fetch_all_positions("active", page_size=100, max_pages=5)
            past = client.fetch_all_positions("past", page_size=100, max_pages=10)
            upsert_positions(active + past)
            positions = get_positions()
        except Exception as e:
            print("Notice: Error fetching positions on demand:", e)

    return calculate_positions_summary(positions, prices)


@app.get("/api/analytics/pnl-calendar")
def api_get_pnl_calendar(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    since: Optional[str] = None,
):
    """
    Return daily PnL calendar breakdown and month statistics.
    """
    effective_from = from_date or (since if since and since != "all" else None)
    trades = get_all_crypto_trades_chronological(since_date=None)
    raw_positions = get_positions(status="past")
    prices = get_market_prices()
    positions = calculate_positions_summary(raw_positions, prices).get("past", [])
    return calculate_daily_pnl_calendar(trades, positions, prices, from_date=effective_from, to_date=to_date)


def sync_btc_history_if_needed():
    """
    Ensure BTC daily closes are cached in SQLite.
    Fetches daily candles via NobitexClient.fetch_udf_history (paginated & rate-limited).
    """
    try:
        now = int(time.time())
        from_ts = now - (365 * 86400)
        client = get_nobitex_client()
        data = client.fetch_udf_history(
            symbol="BTCUSDT",
            resolution="D",
            from_ts=from_ts,
            to_ts=now,
            max_pages=3,
        )
        if data.get("s") == "ok":
            timestamps = data.get("t", [])
            closes = data.get("c", [])
            records = []
            for ts, c in zip(timestamps, closes):
                d_str = ts_to_irst_date_str(ts)
                records.append({"date": d_str, "close_price": float(c)})
            if records:
                upsert_btc_daily_history(records)
    except Exception as e:
        print("Notice: Could not refresh BTC history from Nobitex online (using local cache):", e)


@app.get("/api/analytics/equity-curve")
def api_get_equity_curve(
    timeframe: str = "all",  # '7d', '30d', '90d', 'ytd', 'all', 'custom'
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    since: Optional[str] = None,
    include_futures: bool = False,
    include_cashflows: bool = False,
):
    """
    Return continuous daily equity curve, cumulative PnL, and Bitcoin benchmark comparison.
    By default (Spot tab), strictly reflects pure Spot trading performance without
    leveraged margin positions or fiat bank cashflows.
    """
    try:
        btc_cache = get_btc_daily_history()
        today_str = today_irst_str()
        if not btc_cache or today_str not in btc_cache:
            sync_btc_history_if_needed()
            btc_cache = get_btc_daily_history()
    except Exception:
        btc_cache = get_btc_daily_history()

    trades = get_all_crypto_trades_chronological(since_date=None)
    wallets = get_wallet_snapshots()
    prices = get_market_prices()

    if include_futures:
        raw_positions = get_positions(status="past")
        past_positions = calculate_positions_summary(raw_positions, prices).get("past", [])
    else:
        past_positions = []

    cf_list = get_cashflows(confirmed_only=True) if include_cashflows else []
    snapshots = get_daily_equity_snapshots() if include_cashflows else {}

    return calculate_equity_curve(
        trades=trades,
        past_positions=past_positions,
        wallets=wallets,
        market_prices=prices,
        btc_prices_map=btc_cache,
        from_date=from_date,
        to_date=to_date,
        since_date=since,
        timeframe=timeframe,
        cashflows=cf_list,
        daily_snapshots=snapshots,
    )


@app.get("/api/cashflows")
def api_get_cashflows(
    flow_type: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    confirmed_only: bool = True,
):
    """Return stored deposit/withdrawal cashflows synced from Nobitex."""
    rows = get_cashflows(
        flow_type=flow_type,
        from_date=from_date,
        to_date=to_date,
        confirmed_only=confirmed_only,
    )
    return {"cashflows": rows, "count": len(rows)}


@app.get("/api/trades")
def api_get_trades(
    exclude_fiat_stable: bool = True,
    type: Optional[str] = None,
    execution_type: Optional[str] = None,
    market: Optional[str] = None,
    search: Optional[str] = None,
    tag: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    sort_by: str = "timestamp",
    sort_desc: bool = True,
):
    """Return paginated, filtered trades."""
    trades, total_count = get_trades(
        exclude_fiat_stable=exclude_fiat_stable,
        trade_type=type,
        execution_type=execution_type,
        market=market,
        search=search,
        tag=tag,
        from_date=from_date,
        to_date=to_date,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_desc=sort_desc,
    )

    total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1

    return {
        "trades": trades,
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


class JournalPayload(BaseModel):
    entry_type: str  # 'trade' or 'position'
    target_id: int
    notes: str = ""
    tags: list = []
    rating: int = 0


@app.get("/api/journal")
def api_get_journal(entry_type: Optional[str] = None, target_id: Optional[int] = None):
    """Return trading journal entries and aggregated strategy tag statistics."""
    if entry_type and target_id is not None:
        entry = get_journal_entry(entry_type, target_id)
        return {"entry": entry}

    entries = get_all_journal_entries(entry_type)
    tags_summary = get_journal_tags_summary()
    return {
        "entries": entries,
        "tags_summary": tags_summary,
        "count": len(entries),
    }


@app.post("/api/journal")
def api_save_journal(payload: JournalPayload):
    """Save or update a trading journal note."""
    if payload.entry_type not in {"trade", "position"}:
        raise HTTPException(
            status_code=400,
            detail="Invalid entry_type. Must be 'trade' or 'position'.",
        )
    entry = upsert_journal_entry(
        entry_type=payload.entry_type,
        target_id=payload.target_id,
        notes=payload.notes,
        tags=payload.tags,
        rating=payload.rating,
    )
    return {"status": "ok", "entry": entry}


@app.delete("/api/journal")
def api_delete_journal(entry_type: str, target_id: int):
    """Delete a trading journal note."""
    if entry_type not in {"trade", "position"}:
        raise HTTPException(
            status_code=400,
            detail="Invalid entry_type. Must be 'trade' or 'position'.",
        )
    deleted = delete_journal_entry(entry_type, target_id)
    return {"status": "ok", "deleted": deleted}


def perform_sync(full: bool = False):
    global sync_status
    warnings_list: List[str] = []
    mode = "full" if full else "incremental"
    try:
        sync_status["is_syncing"] = True
        sync_status["last_error"] = None
        sync_status["warnings"] = []
        sync_status["mode"] = mode
        sync_status["step"] = "Connecting to Nobitex..."

        client = get_nobitex_client()

        # 1. Sync wallet balances (spot + margin)
        sync_status["step"] = "Fetching wallet balances..."
        try:
            spot_wallets = client.fetch_wallets(wallet_type="spot")
            upsert_wallet_snapshots(spot_wallets, wallet_type="spot")
            try:
                margin_wallets = client.fetch_wallets(wallet_type="margin")
                upsert_wallet_snapshots(margin_wallets, wallet_type="margin")
            except Exception as mw_err:
                print("Notice: Margin wallets fetch failed:", mw_err)
                warnings_list.append(f"Margin wallets sync skipped: {mw_err}")
        except NobitexAuthError as e:
            warnings_list.append(f"Wallets auth error ({e.code}): {e.message}")
        except Exception as e:
            print("Notice: Wallet fetch failed:", e)
            warnings_list.append(f"Wallets sync failed: {e}")

        # 2. Sync real-time market prices (public)
        sync_status["step"] = "Updating market prices..."
        try:
            prices = client.fetch_market_stats()
            upsert_market_prices(prices)
        except Exception as e:
            print("Notice: Market prices fetch failed:", e)
            warnings_list.append(f"Market prices sync failed: {e}")

        # 3. Sync orders (incremental via fromId when local orders exist)
        sync_status["step"] = "Fetching user orders..."
        try:
            max_order_id = None if full else get_max_order_id()
            if max_order_id is not None:
                open_orders = client.fetch_all_orders(status="open", page_size=100, max_pages=5)
                reconcile_open_orders(open_orders)
                new_orders = client.fetch_incremental_orders(since_id=max_order_id, status="all", page_size=1000)
                if new_orders:
                    upsert_orders(new_orders)
            else:
                orders = client.fetch_all_orders(status="all", page_size=100, max_pages=50)
                if orders:
                    upsert_orders(orders)
        except NobitexAuthError as e:
            warnings_list.append(f"Orders auth error ({e.code}): {e.message}")
        except Exception as e:
            print("Notice: Orders fetch failed:", e)
            warnings_list.append(f"Orders sync failed: {e}")

        # 4. Sync margin & futures positions (full pagination + stale Open reconciliation)
        sync_status["step"] = "Fetching margin positions..."
        pos_count = 0
        try:
            active_pos = client.fetch_all_positions("active", page_size=100, max_pages=5)
            known_closed = None if full else get_known_closed_position_ids()
            past_pos = client.fetch_all_positions(
                "past",
                page_size=100,
                max_pages=10,
                stop_on_known_ids=known_closed,
            )
            pos_count = upsert_positions(active_pos + past_pos)

            # Reconcile any previously Open positions in SQLite that disappeared from live active
            live_active_ids = {int(p["id"]) for p in active_pos if p.get("id") is not None}
            stale_open_ids = get_active_position_ids() - live_active_ids
            reconciled_positions = []
            for stale_id in stale_open_ids:
                try:
                    updated_pos = client.fetch_position_status(stale_id)
                    if updated_pos:
                        reconciled_positions.append(updated_pos)
                except Exception as rec_err:
                    print(f"Notice: Failed reconciling position {stale_id}:", rec_err)
            if reconciled_positions:
                pos_count += upsert_positions(reconciled_positions)
        except NobitexAuthError as e:
            msg = (
                f"Positions skipped ({e.code}): API key may require TRADE permission for /positions/list."
            )
            print("Notice:", msg)
            warnings_list.append(msg)
        except Exception as e:
            print("Notice: Positions fetch failed:", e)
            warnings_list.append(f"Positions sync failed: {e}")

        # 5. Sync trades (incremental via fromId when local trades exist)
        sync_status["step"] = "Syncing trade history..."
        max_trade_id = None if full else get_max_trade_id()
        if max_trade_id is not None:
            trades = client.fetch_incremental_trades(since_id=max_trade_id, page_size=500)
        else:
            trades = client.fetch_all_trades(page_size=500)
        # Pass order_type_map=None so upsert_trades uses the complete orders table in SQLite
        saved_count = upsert_trades(trades, order_type_map=None)

        # 6. Refresh cached market metadata (12h TTL) for live leverage, fees & precisions
        try:
            _, mm_stale = get_metadata_cache("margin_markets_cache", ttl_seconds=43200.0)
            if full or mm_stale:
                mm_data = client.fetch_margin_markets(authenticated=True)
                if mm_data:
                    set_metadata_json("margin_markets_cache", mm_data)
        except Exception as mm_err:
            print("Notice: Margin markets metadata refresh failed:", mm_err)
            warnings_list.append(f"Margin markets metadata skipped: {mm_err}")

        try:
            _, opt_stale = get_metadata_cache("system_options_cache", ttl_seconds=43200.0)
            if full or opt_stale:
                opt_data = client.fetch_system_options()
                if opt_data:
                    set_metadata_json("system_options_cache", opt_data)
        except Exception as opt_err:
            print("Notice: System options metadata refresh failed:", opt_err)
            warnings_list.append(f"System options metadata skipped: {opt_err}")

        # 7. Sync deposits & withdrawals for TWR cashflow reconciliation
        sync_status["step"] = "Syncing deposits & withdrawals..."
        try:
            deposits = client.fetch_all_deposits(page_size=100, max_pages=10)
            if deposits:
                upsert_cashflows(deposits, flow_type="deposit")
        except NobitexAuthError as e:
            warnings_list.append(f"Deposits sync skipped ({e.code}): {e.message}")
        except Exception as dep_err:
            print("Notice: Deposits sync failed:", dep_err)
            warnings_list.append(f"Deposits sync skipped: {dep_err}")

        try:
            withdrawals = client.fetch_all_withdrawals(page_size=100, max_pages=10)
            if withdrawals:
                upsert_cashflows(withdrawals, flow_type="withdraw")
        except NobitexAuthError as e:
            warnings_list.append(f"Withdrawals sync skipped ({e.code}): {e.message}")
        except Exception as wdr_err:
            print("Notice: Withdrawals sync failed:", wdr_err)
            warnings_list.append(f"Withdrawals sync skipped: {wdr_err}")

        # 8. Optionally fetch Nobitex 30-day portfolio daily total profit snapshots
        try:
            _, pf_stale = get_metadata_cache("nobitex_portfolio_profit_cache", ttl_seconds=3600.0)
            if full or pf_stale:
                pf_total = client.fetch_portfolio_daily_total_profit(monthly=True)
                if isinstance(pf_total, dict) and pf_total:
                    set_metadata_json("nobitex_portfolio_profit_cache", pf_total)
                    data_map = pf_total.get("data") if isinstance(pf_total.get("data"), dict) else {}
                    snap_records = []
                    for d_key, val in data_map.items():
                        if isinstance(val, dict) and val.get("totalBalance") is not None:
                            snap_records.append({
                                "date": str(d_key)[:10],
                                "total_equity_usdt": float(val.get("totalBalance", 0.0)),
                                "net_cashflow_usdt": 0.0,
                                "source": "nobitex_portfolio",
                            })
                    if snap_records:
                        upsert_daily_equity_snapshots(snap_records)
        except NobitexAPIError as pf_err:
            if pf_err.code != "FeatureUnavailable":
                warnings_list.append(f"Portfolio daily profit skipped ({pf_err.code}): {pf_err.message}")
        except Exception as pf_err:
            print("Notice: Portfolio daily profit refresh failed:", pf_err)

        record_last_sync()

        sync_status["warnings"] = warnings_list
        sync_status["last_result"] = (
            f"Successfully synced ({mode}) {saved_count} trades, {pos_count} positions, and updated wallets."
        )
    except Exception as e:
        sync_status["last_error"] = str(e)
        sync_status["warnings"] = warnings_list
    finally:
        sync_status["is_syncing"] = False
        sync_status["step"] = None


@app.post("/api/sync")
def api_sync_trades(background_tasks: BackgroundTasks, full: bool = False):
    """Trigger background sync with Nobitex API (incremental by default; pass full=true for full re-sync)."""
    global sync_status
    if sync_status["is_syncing"]:
        return {"status": "in_progress", "message": "Sync is already in progress."}

    sync_status["is_syncing"] = True
    background_tasks.add_task(perform_sync, full=full)
    return {"status": "started", "mode": "full" if full else "incremental", "message": "Synchronization started."}


@app.get("/api/sync/status")
def api_sync_status():
    """Check sync progress status."""
    return sync_status


_prices_refresh_lock = threading.Lock()
_prices_refresh_cache: Dict[str, Any] = {"timestamp": 0.0, "result": None}
PRICES_REFRESH_COOLDOWN_SEC = 5.0


@app.post("/api/prices/refresh")
def api_refresh_prices():
    """
    Fetch real-time market stats from Nobitex public API and update SQLite.
    Uses a 5-second server-side cooldown cache to stay well within the 20 req/min limit.
    """
    now = time.monotonic()
    with _prices_refresh_lock:
        cached_res = _prices_refresh_cache.get("result")
        if cached_res and (now - _prices_refresh_cache["timestamp"] < PRICES_REFRESH_COOLDOWN_SEC):
            return {**cached_res, "cached": True}

    try:
        client = get_nobitex_client()
        fresh_prices = client.fetch_market_stats()
        count = upsert_market_prices(fresh_prices)
        result = {
            "status": "ok",
            "updated_count": count,
            "timestamp": now_irst().isoformat(),
        }
        with _prices_refresh_lock:
            _prices_refresh_cache["timestamp"] = time.monotonic()
            _prices_refresh_cache["result"] = result
        return result
    except NobitexRateLimitError as e:
        return JSONResponse(
            status_code=429,
            content={"status": "error", "code": e.code, "message": e.message, "backoff": e.backoff},
        )
    except NobitexAPIError as e:
        return {"status": "error", "code": e.code, "message": e.message}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/export/csv")
def api_export_csv(
    exclude_fiat_stable: bool = True,
    type: Optional[str] = None,
    execution_type: Optional[str] = None,
    market: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: str = "timestamp",
    lang: str = "en",
):
    """Export filtered trades as downloadable CSV with UTF-8 BOM."""
    trades, _ = get_trades(
        exclude_fiat_stable=exclude_fiat_stable,
        trade_type=type,
        execution_type=execution_type,
        market=market,
        search=search,
        page=1,
        page_size=100000,
        sort_by=sort_by,
        sort_desc=True,
    )

    output = io.StringIO()
    output.write("\ufeff")  # UTF-8 BOM

    fieldnames = [
        "id",
        "order_id",
        "trade_type",
        "market",
        "type",
        "timestamp",
        "price_usdt",
        "amount",
        "net_amount",
        "total_usdt",
        "fee",
        "src_currency",
        "dst_currency",
    ]

    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()

    for t in trades:
        row = dict(t)
        row["price_usdt"] = row.get("price")
        row["total_usdt"] = row.get("total")
        row["net_amount"] = row.get("net_amount") if row.get("net_amount") is not None else ""

        raw_ts = str(row.get("timestamp", ""))
        try:
            clean_ts = raw_ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt_tehran = dt.astimezone(TEHRAN_TZ)
            if lang == "fa" and jdatetime:
                jdt = jdatetime.datetime.fromgregorian(datetime=dt_tehran)
                row["timestamp"] = jdt.strftime("%Y/%m/%d %H:%M:%S")
            else:
                row["timestamp"] = dt_tehran.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass

        writer.writerow(row)

    output.seek(0)
    filename = "nobitex_trades.csv"

    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


class ChartStateRequest(BaseModel):
    symbol: str
    overlays: Optional[List[Dict[str, Any]]] = None
    indicators: Optional[List[str]] = None
    timeframe: Optional[str] = "1D"


# Bounded LRU + TTL candle cache (prevents unbounded memory growth)
MAX_CANDLE_CACHE_ENTRIES = 256
CANDLE_CACHE_TTL_SEC = 60.0
_INTRADAY_7D_RESOLUTIONS = {"1", "5", "15", "30"}
_candle_cache_lock = threading.Lock()
_candle_cache: "OrderedDict[str, Tuple[float, Any]]" = OrderedDict()


def _get_cached_candles(cache_key: str) -> Optional[Any]:
    now_ts = time.time()
    with _candle_cache_lock:
        entry = _candle_cache.get(cache_key)
        if entry is None:
            return None
        expires_at, data = entry
        if expires_at <= now_ts:
            _candle_cache.pop(cache_key, None)
            return None
        _candle_cache.move_to_end(cache_key)
        return data


def _set_cached_candles(cache_key: str, data: Any, ttl_sec: float = CANDLE_CACHE_TTL_SEC) -> None:
    expires_at = time.time() + ttl_sec
    with _candle_cache_lock:
        _candle_cache[cache_key] = (expires_at, data)
        _candle_cache.move_to_end(cache_key)
        while len(_candle_cache) > MAX_CANDLE_CACHE_ENTRIES:
            _candle_cache.popitem(last=False)


@app.get("/api/chart/candles")
def api_get_candles(
    symbol: str = "BTCUSDT",
    resolution: str = "D",
    from_ts: Optional[int] = None,
    to_ts: Optional[int] = None,
):
    """
    Fetch OHLCV candles from Nobitex UDF API with 500-bar pagination, bounded LRU/TTL cache,
    7-day intraday clamp (1/5/15/30m), and weekly aggregation (ADR-006).
    """
    clean_symbol = symbol.replace("-", "").replace("/", "").upper()

    res_input = resolution.strip()
    res_upper = res_input.upper()
    if res_upper in ["1H", "60"]:
        res_norm = "60"
    elif res_upper in ["4H", "240"]:
        res_norm = "240"
    elif res_upper in ["1D", "D"]:
        res_norm = "D"
    elif res_upper in ["1W", "W"]:
        res_norm = "1W"
    else:
        res_norm = res_input

    # Handle restricted currency pairs (USDT/IRT, DAI/IRT)
    if clean_symbol in ["USDTIRT", "DAIIRT"]:
        return {
            "status": "restricted",
            "symbol": clean_symbol,
            "message": "Access restricted by exchange (HTTP 403 Forbidden). Nobitex removed public UDF charts for this pair.",
            "candles": [],
        }

    now = int(time.time())
    if to_ts is None:
        to_ts = now

    # Nobitex Mehr 1405 limit: intraday resolutions (1, 5, 15, 30) only retain last 7 days
    if res_norm in _INTRADAY_7D_RESOLUTIONS:
        min_intraday_from = now - (7 * 86400)
        if to_ts <= min_intraday_from:
            return {
                "status": "no_data",
                "symbol": clean_symbol,
                "resolution": res_norm,
                "candles": [],
            }
        if from_ts is None:
            from_ts = min_intraday_from
        else:
            from_ts = max(int(from_ts), min_intraday_from)
    elif from_ts is None:
        if res_norm == "1W":
            from_ts = to_ts - (5 * 365 * 86400)  # 5 years for weekly
        elif res_norm == "D":
            from_ts = to_ts - (365 * 86400)      # 1 year
        elif res_norm in ["60", "240"]:
            from_ts = to_ts - (60 * 86400)       # 60 days
        else:
            from_ts = to_ts - (7 * 86400)        # 7 days default

    cache_key = f"{clean_symbol}:{res_norm}:{from_ts // 60}:{to_ts // 60}"
    cached = _get_cached_candles(cache_key)
    if cached is not None:
        return cached

    fetch_res = "D" if res_norm == "1W" else res_norm
    fetch_from = from_ts - (7 * 86400) if res_norm == "1W" else from_ts

    client = get_nobitex_client()
    try:
        data = client.fetch_udf_history(
            symbol=clean_symbol,
            resolution=fetch_res,
            from_ts=fetch_from,
            to_ts=to_ts,
            max_pages=10,
        )
    except NobitexAuthError as e:
        if e.status_code == 403:
            return {
                "status": "restricted",
                "symbol": clean_symbol,
                "message": "Access restricted by exchange (HTTP 403 Forbidden)",
                "candles": [],
            }
        raise HTTPException(status_code=502, detail=f"Nobitex UDF Error: HTTP {e.status_code}")
    except NobitexRateLimitError as e:
        raise HTTPException(status_code=429, detail=f"Nobitex UDF Rate Limit: {e.message}")
    except NobitexAPIError as e:
        raise HTTPException(status_code=502, detail=f"Nobitex UDF Error: {e.message}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Nobitex UDF Network Error: {str(e)}")

    if data.get("s") != "ok":
        return {
            "status": "no_data",
            "symbol": clean_symbol,
            "resolution": res_norm,
            "candles": [],
        }

    t_arr = data.get("t", [])
    o_arr = data.get("o", [])
    h_arr = data.get("h", [])
    l_arr = data.get("l", [])
    c_arr = data.get("c", [])
    v_arr = data.get("v", [])

    candles = []
    if res_norm == "1W":
        weeks = defaultdict(list)
        for t, o, h, l, c, v in zip(t_arr, o_arr, h_arr, l_arr, c_arr, v_arr):
            dt = datetime.fromtimestamp(t, tz=timezone.utc)
            start_of_week = dt - timedelta(days=dt.weekday())
            w_ts = int(start_of_week.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
            weeks[w_ts].append((t, o, h, l, c, v))

        for w_ts in sorted(weeks.keys()):
            bars = weeks[w_ts]
            candles.append({
                "timestamp": w_ts * 1000,
                "open": float(bars[0][1]),
                "high": float(max(b[2] for b in bars)),
                "low": float(min(b[3] for b in bars)),
                "close": float(bars[-1][4]),
                "volume": float(sum(b[5] for b in bars)),
            })
    else:
        for t, o, h, l, c, v in zip(t_arr, o_arr, h_arr, l_arr, c_arr, v_arr):
            candles.append({
                "timestamp": int(t) * 1000,
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": float(v) if v is not None else 0.0,
            })

    result = {
        "status": "ok",
        "symbol": clean_symbol,
        "resolution": res_norm,
        "candles": candles,
    }

    _set_cached_candles(cache_key, result)
    return result


@app.get("/api/chart/state")
def api_get_chart_state(symbol: str = "BTCUSDT"):
    """Retrieve saved overlays, indicators, and timeframe for a symbol."""
    return get_chart_state(symbol)


@app.post("/api/chart/state")
def api_save_chart_state(payload: ChartStateRequest):
    """Save overlays, indicators, and timeframe for a symbol."""
    success = save_chart_state(
        symbol=payload.symbol,
        overlays=payload.overlays,
        indicators=payload.indicators,
        timeframe=payload.timeframe,
    )
    return {"status": "ok", "saved": success}


@app.get("/api/chart/symbols")
def api_get_chart_symbols():
    """
    Return list of available markets for charting with latest price and 24h change.
    """
    prices = get_market_prices()
    symbols_list = []

    POPULAR_SYMBOLS = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
        "SUIUSDT", "BNBUSDT", "PEPEUSDT", "SHIBUSDT", "ADAUSDT",
        "AVAXUSDT", "NEARUSDT", "TONUSDT", "LINKUSDT", "TRXUSDT", "LTCUSDT"
    ]

    for m_key, info in prices.items():
        if m_key.endswith("-usdt"):
            base = m_key[:-5].upper()
            sym = f"{base}USDT"
            symbols_list.append({
                "symbol": sym,
                "base": base,
                "quote": "USDT",
                "price": info.get("latest", 0.0),
                "dayChange": info.get("dayChange", 0.0),
                "is_restricted": False,
            })

    # Add restricted pairs with clear flags
    symbols_list.append({
        "symbol": "USDTIRT",
        "base": "USDT",
        "quote": "IRT",
        "price": 0.0,
        "dayChange": 0.0,
        "is_restricted": True,
        "restriction_reason": "محدودیت رگولاتوری نوبیتکس (HTTP 403)",
    })
    symbols_list.append({
        "symbol": "DAIIRT",
        "base": "DAI",
        "quote": "IRT",
        "price": 0.0,
        "dayChange": 0.0,
        "is_restricted": True,
        "restriction_reason": "محدودیت رگولاتوری نوبیتکس (HTTP 403)",
    })

    def sort_key(item):
        sym = item["symbol"]
        if sym in POPULAR_SYMBOLS:
            return (0, POPULAR_SYMBOLS.index(sym))
        if item.get("is_restricted"):
            return (2, sym)
        return (1, sym)

    symbols_list.sort(key=sort_key)
    return symbols_list


# ==========================================
# OPEN ORDERS ENDPOINTS
# ==========================================

class CancelOrderRequest(BaseModel):
    order_id: int


_open_orders_cache = {
    "timestamp": 0.0,
    "data": None
}


@app.get("/api/orders/open")
def api_get_open_orders(refresh: bool = False):
    """
    Fetch all active/open orders with Spot vs Margin separation,
    enriched with current market price, distance to trigger %, and fill progress.
    """
    global _open_orders_cache
    now = time.time()

    # Use cached data if within 10 seconds unless refresh is requested
    if not refresh and _open_orders_cache["data"] and (now - _open_orders_cache["timestamp"] < 10.0):
        return _open_orders_cache["data"]

    client = get_nobitex_client()
    raw_orders = []
    source = "live"
    try:
        raw_orders = client.fetch_all_orders(status="open", page_size=100)
        reconcile_open_orders(raw_orders)
    except Exception as e:
        print("Warning: Failed to fetch live open orders from Nobitex API:", e)
        source = "db"
        raw_orders = get_open_orders()

    prices_map = get_market_prices()

    enriched_orders = []
    spot_count = 0
    margin_count = 0
    buy_count = 0
    sell_count = 0
    total_val_usdt = 0.0
    total_val_rls = 0.0

    for o in raw_orders:
        oid = o.get("id")
        trade_type = o.get("tradeType") or o.get("trade_type", "Spot")
        mkt_raw = str(o.get("market", "")).upper()
        parts = mkt_raw.replace("/", "-").split("-")
        base = parts[0] if len(parts) > 0 else ""
        quote = parts[1] if len(parts) > 1 else "USDT"

        side = str(o.get("type", "")).lower()
        execution = o.get("execution", "Limit")

        def to_f(v, d=0.0):
            if v is None:
                return d
            try:
                return float(v)
            except (ValueError, TypeError):
                return d

        price = to_f(o.get("price"))
        amount = to_f(o.get("amount"))
        matched = to_f(o.get("matchedAmount") or o.get("matched_amount"))
        unmatched = to_f(o.get("unmatchedAmount") or o.get("unmatched_amount"), max(0.0, amount - matched))
        tot_price = to_f(o.get("totalPrice") or o.get("totalOrderPrice") or o.get("total_price"))
        if tot_price == 0.0 and price > 0 and amount > 0:
            tot_price = price * amount

        leverage = to_f(o.get("leverage"), 1.0)
        status = o.get("status", "Active")
        created_at = o.get("created_at")

        # Current market price lookup
        m_key = f"{base.lower()}-{quote.lower()}"
        cur_price_obj = prices_map.get(m_key, {})
        cur_price = cur_price_obj.get("latest") if isinstance(cur_price_obj, dict) else None

        # Distance calculation
        dist_pct = None
        if cur_price and cur_price > 0 and price > 0:
            dist_pct = round(((price - cur_price) / cur_price) * 100, 2)

        fill_pct = 0.0
        if amount > 0:
            fill_pct = round((matched / amount) * 100, 1)

        if trade_type == "Margin":
            margin_count += 1
        else:
            spot_count += 1

        if side == "buy":
            buy_count += 1
        elif side == "sell":
            sell_count += 1

        if quote == "USDT":
            total_val_usdt += tot_price
        elif quote in ("RLS", "IRT"):
            total_val_rls += tot_price

        enriched_orders.append({
            "id": oid,
            "market": mkt_raw,
            "base": base,
            "quote": quote,
            "trade_type": trade_type,
            "type": side,
            "execution": execution,
            "price": price,
            "current_price": cur_price,
            "distance_percent": dist_pct,
            "amount": amount,
            "matched_amount": matched,
            "unmatched_amount": unmatched,
            "fill_percent": fill_pct,
            "total_price": tot_price,
            "leverage": leverage,
            "status": status,
            "created_at": created_at,
        })

    # Sort orders by created_at descending (newest first)
    enriched_orders.sort(key=lambda x: str(x.get("created_at", "")), reverse=True)

    summary = {
        "total_count": len(enriched_orders),
        "spot_count": spot_count,
        "margin_count": margin_count,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "total_value_usdt": round(total_val_usdt, 2),
        "total_value_rls": round(total_val_rls, 0),
    }

    result = {
        "orders": enriched_orders,
        "summary": summary,
        "source": source,
        "updated_at": now_irst().isoformat(),
    }
    _open_orders_cache = {"timestamp": now, "data": result}
    return result


@app.post("/api/orders/cancel")
def api_cancel_order(req: CancelOrderRequest):
    """
    Cancel an open order on Nobitex and update local SQLite status.
    """
    global _open_orders_cache
    if not req.order_id:
        raise HTTPException(status_code=400, detail="order_id is required")

    client = get_nobitex_client()
    try:
        res = client.cancel_order(req.order_id)
        update_order_status(req.order_id, "Canceled")
        _open_orders_cache = {"timestamp": 0.0, "data": None}
        return {"status": "ok", "message": "سفارش با موفقیت لغو شد.", "result": res}
    except NobitexAuthError as e:
        msg = "کلید API شما دسترسی لغو سفارش (TRADE) ندارد یا نامعتبر است."
        return JSONResponse(
            status_code=403,
            content={"status": "failed", "code": e.code, "message": msg, "detail": msg},
        )
    except NobitexRateLimitError as e:
        msg = f"محدودیت فراخوانی نوبیتکس؛ لطفاً {int(e.backoff or 5)} ثانیه صبر کنید."
        return JSONResponse(
            status_code=429,
            content={"status": "failed", "code": e.code, "message": msg, "detail": msg, "backoff": e.backoff},
        )
    except NobitexAPIError as e:
        http_status = e.status_code if 400 <= e.status_code < 500 else 422
        return JSONResponse(
            status_code=http_status,
            content={"status": "failed", "code": e.code, "message": e.message, "detail": e.message},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ClosePositionRequest(BaseModel):
    execution: str = "market"  # limit, market, stop_limit, stop_market, oco
    amount: float
    price: Optional[float] = None
    stop_price: Optional[float] = None
    stop_limit_price: Optional[float] = None


class EditCollateralRequest(BaseModel):
    collateral: float


class CancelOldOrdersRequest(BaseModel):
    hours: Optional[float] = None
    execution: Optional[str] = None
    trade_type: Optional[str] = None
    src_currency: Optional[str] = None
    dst_currency: Optional[str] = None


def _map_trade_action_error(e: Exception) -> JSONResponse:
    """Standardized error mapping for TRADE-permission endpoints (zero retries on 401/403)."""
    if isinstance(e, NobitexAuthError):
        msg = "کلید API شما دسترسی معامله (TRADE) ندارد یا نامعتبر است. (TRADE permission required)"
        return JSONResponse(
            status_code=403,
            content={"status": "failed", "code": "TradePermissionRequired", "upstream_code": e.code, "message": msg, "detail": msg},
        )
    if isinstance(e, NobitexRateLimitError):
        msg = f"محدودیت فراخوانی نوبیتکس؛ لطفاً {int(e.backoff or 5)} ثانیه صبر کنید."
        return JSONResponse(
            status_code=429,
            content={"status": "failed", "code": e.code, "message": msg, "detail": msg, "backoff": e.backoff},
        )
    if isinstance(e, NobitexAPIError):
        http_status = e.status_code if 400 <= e.status_code < 500 else 422
        return JSONResponse(
            status_code=http_status,
            content={"status": "failed", "code": e.code, "message": e.message, "detail": e.message},
        )
    return JSONResponse(status_code=500, content={"status": "failed", "code": "InternalError", "message": str(e), "detail": str(e)})


@app.post("/api/positions/{position_id}/close")
def api_close_position(position_id: int, req: ClosePositionRequest):
    """Submit a closing order for an open margin position (requires TRADE permission)."""
    exec_norm = req.execution.strip().lower()
    if exec_norm not in {"limit", "market", "stop_limit", "stop_market", "oco"}:
        raise HTTPException(status_code=400, detail="Invalid execution type. Must be limit, market, stop_limit, stop_market, or oco.")
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be greater than 0.")
    if exec_norm in {"limit", "stop_limit", "oco"} and (req.price is None or req.price <= 0):
        raise HTTPException(status_code=400, detail=f"price is required and must be > 0 for execution='{exec_norm}'.")
    if exec_norm in {"stop_limit", "stop_market", "oco"} and (req.stop_price is None or req.stop_price <= 0):
        raise HTTPException(status_code=400, detail=f"stop_price is required and must be > 0 for execution='{exec_norm}'.")

    client = get_nobitex_client()
    try:
        res = client.close_position(
            position_id=position_id,
            execution=exec_norm,
            amount=req.amount,
            price=req.price,
            stop_price=req.stop_price,
            stop_limit_price=req.stop_limit_price,
        )
        try:
            updated = client.fetch_position_status(position_id)
            if updated:
                upsert_positions([updated])
        except Exception:
            pass
        return {"status": "ok", "message": "سفارش بستن موقعیت ثبت شد.", "result": res}
    except Exception as e:
        return _map_trade_action_error(e)


@app.post("/api/positions/{position_id}/edit-collateral")
def api_edit_position_collateral(position_id: int, req: EditCollateralRequest):
    """Increase or decrease collateral for an open margin position (requires TRADE permission)."""
    if req.collateral <= 0:
        raise HTTPException(status_code=400, detail="collateral must be greater than 0.")

    client = get_nobitex_client()
    try:
        res = client.edit_position_collateral(position_id=position_id, collateral=req.collateral)
        pos_obj = res.get("position") if isinstance(res, dict) else None
        if isinstance(pos_obj, dict) and pos_obj.get("id"):
            upsert_positions([pos_obj])
        else:
            try:
                updated = client.fetch_position_status(position_id)
                if updated:
                    upsert_positions([updated])
            except Exception:
                pass
        return {"status": "ok", "message": "وجه تضمین موقعیت با موفقیت به‌روزرسانی شد.", "result": res}
    except Exception as e:
        return _map_trade_action_error(e)


@app.post("/api/orders/cancel-old")
def api_cancel_old_orders(req: CancelOldOrdersRequest):
    """Bulk-cancel active orders matching filters via POST /market/orders/cancel-old (TRADE permission)."""
    if req.hours is not None and req.hours < 0:
        raise HTTPException(status_code=400, detail="hours must be >= 0.")
    if req.execution and req.execution.strip().lower() not in {"limit", "market", "stop_limit", "stop_market"}:
        raise HTTPException(status_code=400, detail="Invalid execution filter.")
    if req.trade_type and req.trade_type.strip().lower() not in {"spot", "margin"}:
        raise HTTPException(status_code=400, detail="Invalid trade_type filter. Must be 'spot' or 'margin'.")

    client = get_nobitex_client()
    try:
        res = client.cancel_old_orders(
            hours=req.hours,
            execution=req.execution,
            trade_type=req.trade_type,
            src_currency=req.src_currency,
            dst_currency=req.dst_currency,
        )
        _invalidate_open_orders_cache()
        return {"status": "ok", "message": "درخواست لغو گروهی سفارش‌ها با موفقیت ثبت شد.", "result": res}
    except Exception as e:
        return _map_trade_action_error(e)


@app.get("/api/user/profile")
def api_get_user_profile(refresh: bool = False):
    """
    Return user account level/VIP tier & API key configuration status (cached 6h in SQLite).
    Works cleanly offline using cached metadata or public fallback.
    """
    client = get_nobitex_client()
    has_keys = bool(client.api_key and client.secret)
    cached_data, is_stale = get_metadata_cache("user_profile_tier_cache", ttl_seconds=21600.0)

    if cached_data and not refresh and not is_stale:
        return {**cached_data, "has_api_keys": has_keys, "cached": True}

    if not has_keys:
        return {
            "has_api_keys": False,
            "username": None,
            "level": None,
            "vip_level": None,
            "limitations": {},
            "cached": False,
        }

    profile: Dict[str, Any] = {}
    limitations: Dict[str, Any] = {}
    try:
        profile = client.fetch_user_profile()
    except Exception:
        if cached_data:
            return {**cached_data, "has_api_keys": has_keys, "cached": True}
    try:
        lim_res = client.fetch_user_limitations()
        limitations = lim_res.get("limitations") if isinstance(lim_res.get("limitations"), dict) else lim_res
    except Exception:
        pass

    options = profile.get("options") if isinstance(profile.get("options"), dict) else {}
    user_level = (
        limitations.get("userLevel")
        if isinstance(limitations, dict) and limitations.get("userLevel") is not None
        else profile.get("level")
    )
    vip_level = (
        profile.get("vipLevel")
        or options.get("vipLevel")
        or (limitations.get("vipLevel") if isinstance(limitations, dict) else None)
    )

    payload = {
        "has_api_keys": has_keys,
        "username": profile.get("username") or profile.get("email"),
        "level": user_level,
        "vip_level": vip_level,
        "limitations": limitations if isinstance(limitations, dict) else {},
        "cached": False,
    }
    if profile or limitations:
        set_metadata_json("user_profile_tier_cache", payload)
    return payload


# ==========================================
# REAL-TIME LOCAL WEBSOCKET BRIDGE
# ==========================================

@app.get("/api/ws/status")
def api_ws_status():
    """Return diagnostic status of the backend Nobitex Centrifugo WebSocket bridge."""
    return get_ws_bridge().get_status()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Local WebSocket endpoint (ws://127.0.0.1:8080/ws) for Vue 3 dashboard tabs.
    Proxies normalized real-time market stats, candles, and private order/trade updates
    from NobitexWSBridge while honoring ADR-012 (browser never connects externally).
    """
    await websocket.accept()
    bridge = get_ws_bridge()
    await bridge.register_client(websocket)
    try:
        while True:
            msg = await websocket.receive_json()
            await bridge.handle_client_message(websocket, msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await bridge.unregister_client(websocket)


# Serve index.html
@app.get("/", response_class=HTMLResponse)
def serve_index():
    index_file = STATIC_DIR / "index.html"
    if not index_file.is_file():
        raise HTTPException(status_code=404, detail="Frontend file not found.")
    return index_file.read_text(encoding="utf-8")


# Mount static directory and TradingView packages
if STATIC_DIR.is_dir():
    tv_dir = STATIC_DIR / "vendor" / "tradingview"
    if tv_dir.is_dir():
        app.mount("/Packages/TradingView", StaticFiles(directory=str(tv_dir)), name="tv_packages")
        app.mount("/static/tradingview", StaticFiles(directory=str(tv_dir)), name="tv_static")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def main():
    print("🚀 Starting Nobitex Portfolio Web Server at http://127.0.0.1:8080 ...")
    webbrowser.open("http://127.0.0.1:8080")
    uvicorn.run("nob.server:app", host="127.0.0.1", port=8080, reload=False)


if __name__ == "__main__":
    main()
