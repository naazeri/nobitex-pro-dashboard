import csv
import io
import logging
import math
import re
import threading
import time
import webbrowser
from collections import OrderedDict, defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import uvicorn
from fastapi import (
    BackgroundTasks,
    FastAPI,
    HTTPException,
    Path as FastAPIPath,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from nob.db import (
    init_db,
    upsert_trades,
    get_trades,
    iter_trades_for_export,
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
    get_unlinked_trade_order_ids,
    reconcile_trade_types_from_orders,
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
from nob.time_utils import IRST, now_irst, today_irst_str, ts_to_irst_date_str
from nob.ws_bridge import NobitexWSBridge

try:
    import jdatetime
except ImportError:
    jdatetime = None

logger = logging.getLogger(__name__)
TEHRAN_TZ = IRST

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"

ALLOWED_LOCAL_HOSTS = {"127.0.0.1", "localhost", "testserver"}
_CANDLE_SYMBOL_RE = re.compile(r"^[A-Z0-9_-]{2,24}$")
_ALLOWED_CANDLE_RESOLUTIONS = {"1", "5", "15", "30", "60", "180", "240", "360", "720", "D", "1W"}

_nobitex_client: Optional[NobitexClient] = None
_client_lock = threading.Lock()
_ws_bridge: Optional[NobitexWSBridge] = None

_open_orders_cache_lock = threading.Lock()
_open_orders_cache: Dict[str, Any] = {"timestamp": 0.0, "data": None, "version": 0}

_on_demand_positions_lock = threading.Lock()
_last_positions_on_demand_ts = 0.0
_ON_DEMAND_POSITIONS_COOLDOWN_SEC = 600.0

_on_demand_btc_lock = threading.Lock()
_last_btc_on_demand_ts = 0.0
_ON_DEMAND_BTC_COOLDOWN_SEC = 600.0


def _is_allowed_local_origin(origin: Optional[str], host: Optional[str] = None) -> bool:
    """Validate that an Origin header points to a permitted localhost origin."""
    if origin is None:
        return True
    try:
        parsed = urlparse(origin.strip())
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in ALLOWED_LOCAL_HOSTS:
        return False
    if host:
        host_clean = host.strip()
        if ":" in host_clean:
            _, port_str = host_clean.rsplit(":", 1)
            host_port = int(port_str) if port_str.isdigit() else None
        else:
            host_port = 443 if parsed.scheme == "https" else 80
        if host_port is not None:
            origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
            if origin_port != host_port:
                return False
    return True


def _format_decimal_str(val: float) -> str:
    """Format a finite positive float as a plain decimal string (no scientific notation)."""
    if not math.isfinite(val):
        raise ValueError("Value must be finite")
    try:
        d = Decimal(str(val))
    except InvalidOperation as exc:
        raise ValueError("Invalid numeric value") from exc
    text = format(d.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def get_nobitex_client() -> NobitexClient:
    """Return shared singleton NobitexClient (lazy-initialized, public-safe)."""
    global _nobitex_client
    if _nobitex_client is None:
        with _client_lock:
            if _nobitex_client is None:
                _nobitex_client = NobitexClient(require_auth=False)
    return _nobitex_client


def _invalidate_open_orders_cache() -> None:
    with _open_orders_cache_lock:
        _open_orders_cache["timestamp"] = 0.0
        _open_orders_cache["data"] = None
        _open_orders_cache["version"] += 1


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


app = FastAPI(title="Nobitex Portfolio & Trades", version="2.2.0", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


@app.middleware("http")
async def _enforce_local_origin_on_mutations(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        host = request.headers.get("host")
        if origin is not None and not _is_allowed_local_origin(origin, host):
            return JSONResponse(status_code=403, content={"detail": "Cross-origin mutating request rejected."})
    return await call_next(request)


# Global sync status protected by _sync_lock
_sync_lock = threading.Lock()
sync_status: Dict[str, Any] = {
    "is_syncing": False,
    "last_result": None,
    "last_error": None,
    "warnings": [],
    "mode": None,
    "step": None,
}


def _update_sync_status(**kwargs: Any) -> None:
    with _sync_lock:
        sync_status.update(kwargs)


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
    Guards on-demand empty-DB fetch with a 10-minute cooldown so Spot-only accounts
    never burn the 30 req/10 min Nobitex positions quota on repeated GET requests.
    """
    global _last_positions_on_demand_ts
    prices = get_market_prices()
    positions = get_positions()

    if not positions:
        should_fetch = False
        now_m = time.monotonic()
        with _on_demand_positions_lock:
            if (now_m - _last_positions_on_demand_ts) >= _ON_DEMAND_POSITIONS_COOLDOWN_SEC:
                _last_positions_on_demand_ts = now_m
                should_fetch = True
        if should_fetch:
            try:
                client = get_nobitex_client()
                active = client.fetch_all_positions("active", page_size=100, max_pages=5)
                past = client.fetch_all_positions("past", page_size=100, max_pages=10)
                upsert_positions(active + past)
                positions = get_positions()
            except Exception as e:
                logger.warning("Error fetching positions on demand: %s", e)

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
    cashflows = get_cashflows(confirmed_only=True)
    return calculate_daily_pnl_calendar(
        trades,
        positions,
        prices,
        from_date=effective_from,
        to_date=to_date,
        cashflows=cashflows,
    )


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
            should_fetch_btc = False
            now_m = time.monotonic()
            with _on_demand_btc_lock:
                global _last_btc_on_demand_ts
                if (now_m - _last_btc_on_demand_ts) >= _ON_DEMAND_BTC_COOLDOWN_SEC:
                    _last_btc_on_demand_ts = now_m
                    should_fetch_btc = True
            if should_fetch_btc:
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

    all_confirmed_cf = get_cashflows(confirmed_only=True)
    cf_list = all_confirmed_cf if include_cashflows else []
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
        inventory_cashflows=all_confirmed_cf,
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
    symbol: Optional[str] = None,
    search: Optional[str] = None,
    tag: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort_by: str = "timestamp",
    sort_desc: bool = True,
):
    """Return paginated, filtered trades."""
    trades, total_count = get_trades(
        exclude_fiat_stable=exclude_fiat_stable,
        trade_type=type,
        execution_type=execution_type,
        market=market,
        symbol=symbol,
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
    tags: List[str] = Field(default_factory=list)
    rating: int = Field(0, ge=0, le=5)


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
    warnings_list: List[str] = []
    mode = "full" if full else "incremental"
    try:
        _update_sync_status(
            is_syncing=True,
            last_error=None,
            warnings=[],
            mode=mode,
            step="Connecting to Nobitex...",
        )

        client = get_nobitex_client()

        # 1. Sync wallet balances (spot + margin)
        _update_sync_status(step="Fetching wallet balances...")
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
        _update_sync_status(step="Updating market prices...")
        prices_map: Dict[str, Any] = {}
        try:
            prices_map = client.fetch_market_stats()
            upsert_market_prices(prices_map)
        except Exception as e:
            print("Notice: Market prices fetch failed:", e)
            warnings_list.append(f"Market prices sync failed: {e}")

        # 3. Sync orders (incremental via dedicated orders cursor in metadata)
        _update_sync_status(step="Fetching user orders...")
        try:
            open_orders = client.fetch_all_orders(status="open", page_size=100, max_pages=5)
            reconcile_open_orders(open_orders)

            cursor_val = None if full else get_metadata_json("orders_sync_cursor_id")
            if cursor_val is not None:
                cursor_int = int(cursor_val)
                new_orders = client.fetch_incremental_orders(since_id=cursor_int, status="all", page_size=1000)
                if new_orders:
                    upsert_orders(new_orders)
                    max_new = max((int(o["id"]) for o in new_orders if o.get("id") is not None), default=cursor_int)
                    set_metadata_json("orders_sync_cursor_id", max(cursor_int, max_new))
            else:
                orders = client.fetch_all_orders(status="all", page_size=100, max_pages=50)
                if orders:
                    upsert_orders(orders)
                    max_id = max((int(o["id"]) for o in orders if o.get("id") is not None), default=None)
                    if max_id is not None:
                        set_metadata_json("orders_sync_cursor_id", max_id)
        except NobitexAuthError as e:
            warnings_list.append(f"Orders auth error ({e.code}): {e.message}")
        except Exception as e:
            print("Notice: Orders fetch failed:", e)
            warnings_list.append(f"Orders sync failed: {e}")

        # 4. Sync margin & futures positions (full pagination + stale Open reconciliation)
        _update_sync_status(step="Fetching margin positions...")
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
        _update_sync_status(step="Syncing trade history...")
        max_trade_id = None if full else get_max_trade_id()
        if max_trade_id is not None:
            trades = client.fetch_incremental_trades(since_id=max_trade_id, page_size=500)
        else:
            trades = client.fetch_all_trades(page_size=500)
        # Pass order_type_map=None so upsert_trades uses the complete orders table in SQLite
        saved_count = upsert_trades(trades, order_type_map=None)

        # Backfill any trades missing their linked order, then reconcile Spot vs Margin classification
        try:
            missing_oids = get_unlinked_trade_order_ids(limit=50)
            if missing_oids:
                fetched_missing: List[Dict[str, Any]] = []
                for oid in missing_oids:
                    try:
                        order_obj = client.fetch_order_status(oid)
                        if isinstance(order_obj, dict) and order_obj.get("id"):
                            fetched_missing.append(order_obj)
                    except (NobitexAuthError, NobitexRateLimitError) as stop_err:
                        warnings_list.append(
                            f"Order backfill halted ({stop_err.code}): {stop_err.message}"
                        )
                        break
                    except Exception as oid_err:
                        logger.debug("Order status lookup failed for %s: %s", oid, oid_err)
                if fetched_missing:
                    upsert_orders(fetched_missing)
            reconcile_trade_types_from_orders()
        except Exception as rec_trade_err:
            logger.warning("Order-trade reconciliation skipped: %s", rec_trade_err)

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

        # 7. Sync deposits & withdrawals for cashflow & crypto inventory reconciliation
        _update_sync_status(step="Syncing deposits & withdrawals...")
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
            _, tx_dep_stale = get_metadata_cache("crypto_deposits_tx_sync", ttl_seconds=21600.0)
            if full or tx_dep_stale:
                tx_deps = client.fetch_deposit_transactions(max_pages=3, page_size=100)
                crypto_tx_deps = [
                    d for d in tx_deps
                    if str(d.get("currency") or "").strip().upper() not in ("", "RLS", "IRR", "IRT")
                ]
                if crypto_tx_deps:
                    upsert_cashflows(crypto_tx_deps, flow_type="deposit")
                set_metadata_json("crypto_deposits_tx_sync", {"count": len(crypto_tx_deps)})
        except NobitexAuthError as e:
            warnings_list.append(f"Crypto deposits sync skipped ({e.code}): {e.message}")
        except Exception as tx_dep_err:
            print("Notice: Crypto deposits sync skipped:", tx_dep_err)

        try:
            withdrawals = client.fetch_all_withdrawals(page_size=100, max_pages=10)
            if withdrawals:
                upsert_cashflows(withdrawals, flow_type="withdraw")
        except NobitexAuthError as e:
            warnings_list.append(f"Withdrawals sync skipped ({e.code}): {e.message}")
        except Exception as wdr_err:
            print("Notice: Withdrawals sync failed:", wdr_err)
            warnings_list.append(f"Withdrawals sync skipped: {wdr_err}")

        # 8. Optionally fetch Nobitex 30-day portfolio daily profit & balance snapshots
        try:
            _, pf_stale = get_metadata_cache("nobitex_portfolio_profit_cache", ttl_seconds=3600.0)
            if full or pf_stale:
                pf_daily = client.fetch_portfolio_daily_profit(monthly=True)
                if isinstance(pf_daily, dict) and pf_daily:
                    set_metadata_json("nobitex_portfolio_profit_cache", pf_daily)
                    prices_for_rls = prices_map or get_market_prices()
                    usdt_rls_entry = prices_for_rls.get("usdt-rls") or prices_for_rls.get("usdtrls") or {}
                    try:
                        usdt_rls_val = float(usdt_rls_entry.get("latest", 0.0)) if isinstance(usdt_rls_entry, dict) else 0.0
                    except (ValueError, TypeError):
                        usdt_rls_val = 0.0

                    snap_records = []
                    raw_data = pf_daily.get("data")
                    if isinstance(raw_data, list):
                        for item in raw_data:
                            if not isinstance(item, dict):
                                continue
                            d_str = str(item.get("report_date") or item.get("date") or "")[:10]
                            bal_raw = item.get("total_balance") if item.get("total_balance") is not None else item.get("totalBalance")
                            if d_str and bal_raw is not None and usdt_rls_val > 0:
                                try:
                                    bal_rls = float(bal_raw)
                                    if bal_rls > 0:
                                        snap_records.append({
                                            "date": d_str,
                                            "total_equity_usdt": round(bal_rls / usdt_rls_val, 2),
                                            "net_cashflow_usdt": 0.0,
                                            "source": "nobitex_portfolio",
                                        })
                                except (ValueError, TypeError):
                                    pass
                    elif isinstance(raw_data, dict):
                        for d_key, val in raw_data.items():
                            if isinstance(val, dict):
                                bal_raw = val.get("total_balance") if val.get("total_balance") is not None else val.get("totalBalance")
                                if bal_raw is not None:
                                    try:
                                        bal_num = float(bal_raw)
                                        eq_usdt = (bal_num / usdt_rls_val) if (val.get("total_balance") is not None and usdt_rls_val > 0) else bal_num
                                        snap_records.append({
                                            "date": str(d_key)[:10],
                                            "total_equity_usdt": round(eq_usdt, 2),
                                            "net_cashflow_usdt": 0.0,
                                            "source": "nobitex_portfolio",
                                        })
                                    except (ValueError, TypeError):
                                        pass
                    if snap_records:
                        upsert_daily_equity_snapshots(snap_records)
        except NobitexAPIError as pf_err:
            if pf_err.code != "FeatureUnavailable":
                warnings_list.append(f"Portfolio daily profit skipped ({pf_err.code}): {pf_err.message}")
        except Exception as pf_err:
            print("Notice: Portfolio daily profit refresh failed:", pf_err)

        record_last_sync()

        _update_sync_status(
            warnings=warnings_list,
            last_result=(
                f"Successfully synced ({mode}) {saved_count} trades, {pos_count} positions, and updated wallets."
            ),
        )
    except Exception as e:
        logger.exception("Sync failed")
        _update_sync_status(last_error=str(e), warnings=warnings_list)
    finally:
        _update_sync_status(is_syncing=False, step=None)


@app.post("/api/sync")
def api_sync_trades(background_tasks: BackgroundTasks, full: bool = False):
    """Trigger background sync with Nobitex API (incremental by default; pass full=true for full re-sync)."""
    with _sync_lock:
        if sync_status["is_syncing"]:
            return {"status": "in_progress", "message": "Sync is already in progress."}
        sync_status["is_syncing"] = True
        sync_status["mode"] = "full" if full else "incremental"
        sync_status["step"] = "Queued..."

    background_tasks.add_task(perform_sync, full=full)
    return {"status": "started", "mode": "full" if full else "incremental", "message": "Synchronization started."}


@app.get("/api/sync/status")
def api_sync_status():
    """Check sync progress status (thread-safe snapshot)."""
    with _sync_lock:
        return {**sync_status, "warnings": list(sync_status.get("warnings") or [])}


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
    except Exception:
        logger.exception("Unexpected error refreshing prices")
        return JSONResponse(
            status_code=502,
            content={"status": "error", "message": "Failed to refresh market prices."},
        )


@app.get("/api/export/csv")
def api_export_csv(
    exclude_fiat_stable: bool = True,
    type: Optional[str] = None,
    execution_type: Optional[str] = None,
    market: Optional[str] = None,
    symbol: Optional[str] = None,
    search: Optional[str] = None,
    tag: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    lang: str = "en",
):
    """Stream filtered trades as downloadable CSV with UTF-8 BOM in bounded DB batches."""
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

    def _csv_stream():
        buf = io.StringIO()
        buf.write("\ufeff")  # UTF-8 BOM
        writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)

        for batch in iter_trades_for_export(
            exclude_fiat_stable=exclude_fiat_stable,
            trade_type=type,
            execution_type=execution_type,
            market=market,
            symbol=symbol,
            search=search,
            tag=tag,
            from_date=from_date,
            to_date=to_date,
            batch_size=500,
        ):
            for t in batch:
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
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    return StreamingResponse(
        _csv_stream(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=nobitex_trades.csv"},
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
    clean_symbol = symbol.replace("-", "").replace("/", "").strip().upper()
    if not _CANDLE_SYMBOL_RE.match(clean_symbol):
        raise HTTPException(status_code=400, detail="Invalid symbol format.")

    res_input = resolution.strip()
    res_upper = res_input.upper()
    res_aliases = {
        "1M": "1",
        "5M": "5",
        "15M": "15",
        "30M": "30",
        "1H": "60",
        "3H": "180",
        "4H": "240",
        "6H": "360",
        "12H": "720",
        "1D": "D",
        "W": "1W",
    }
    res_norm = res_aliases.get(res_upper, res_upper)
    if res_norm not in _ALLOWED_CANDLE_RESOLUTIONS:
        raise HTTPException(status_code=400, detail="Unsupported candle resolution.")

    if from_ts is not None and from_ts <= 0:
        raise HTTPException(status_code=400, detail="from_ts must be positive.")
    if to_ts is not None and to_ts <= 0:
        raise HTTPException(status_code=400, detail="to_ts must be positive.")
    if from_ts is not None and to_ts is not None and from_ts >= to_ts:
        raise HTTPException(status_code=400, detail="from_ts must be less than to_ts.")

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
    except Exception:
        logger.exception("Nobitex UDF network error for %s", clean_symbol)
        raise HTTPException(status_code=502, detail="Nobitex UDF Network Error")

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
        for t, o, h, low_p, c, v in zip(t_arr, o_arr, h_arr, l_arr, c_arr, v_arr):
            dt = datetime.fromtimestamp(t, tz=timezone.utc)
            start_of_week = dt - timedelta(days=dt.weekday())
            w_ts = int(start_of_week.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
            weeks[w_ts].append((t, o, h, low_p, c, v))

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
        for t, o, h, low_p, c, v in zip(t_arr, o_arr, h_arr, l_arr, c_arr, v_arr):
            candles.append({
                "timestamp": int(t) * 1000,
                "open": float(o),
                "high": float(h),
                "low": float(low_p),
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
    order_id: int = Field(..., gt=0)


@app.get("/api/orders/open")
def api_get_open_orders(refresh: bool = False):
    """
    Fetch all active/open orders with Spot vs Margin separation,
    enriched with current market price, distance to trigger %, and fill progress.
    """
    now = time.monotonic()

    with _open_orders_cache_lock:
        cached_data = _open_orders_cache["data"]
        cached_ts = _open_orders_cache["timestamp"]
        start_version = _open_orders_cache["version"]
        if not refresh and cached_data and (now - cached_ts < 10.0):
            return cached_data

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
    with _open_orders_cache_lock:
        if _open_orders_cache["version"] == start_version:
            _open_orders_cache["timestamp"] = time.monotonic()
            _open_orders_cache["data"] = result
    return result


@app.post("/api/orders/cancel")
def api_cancel_order(req: CancelOrderRequest):
    """
    Cancel an open order on Nobitex and update local SQLite status.
    """
    client = get_nobitex_client()
    try:
        res = client.cancel_order(req.order_id)
        update_order_status(req.order_id, "Canceled")
        _invalidate_open_orders_cache()
        return {"status": "ok", "message": "سفارش با موفقیت لغو شد.", "result": res}
    except Exception as e:
        return _map_trade_action_error(e)


class ClosePositionRequest(BaseModel):
    execution: str = "market"  # limit, market, stop_limit, stop_market, oco
    amount: float = Field(..., gt=0, allow_inf_nan=False)
    price: Optional[float] = Field(None, gt=0, allow_inf_nan=False)
    stop_price: Optional[float] = Field(None, gt=0, allow_inf_nan=False)
    stop_limit_price: Optional[float] = Field(None, gt=0, allow_inf_nan=False)


class EditCollateralRequest(BaseModel):
    collateral: float = Field(..., gt=0, allow_inf_nan=False)


class CancelOldOrdersRequest(BaseModel):
    hours: Optional[float] = Field(None, ge=0, allow_inf_nan=False)
    execution: Optional[str] = None
    trade_type: Optional[str] = None
    src_currency: Optional[str] = None
    dst_currency: Optional[str] = None
    confirm_all: bool = False


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
    logger.exception("Unexpected error in TRADE action")
    return JSONResponse(
        status_code=500,
        content={"status": "failed", "code": "InternalError", "message": "Internal server error", "detail": "Internal server error"},
    )


@app.post("/api/positions/{position_id}/close")
def api_close_position(
    req: ClosePositionRequest,
    position_id: int = FastAPIPath(..., gt=0),
):
    """Submit a closing order for an open margin position (requires TRADE permission)."""
    exec_norm = req.execution.strip().lower()
    if exec_norm not in {"limit", "market", "stop_limit", "stop_market", "oco"}:
        raise HTTPException(status_code=400, detail="Invalid execution type. Must be limit, market, stop_limit, stop_market, or oco.")
    if exec_norm in {"limit", "stop_limit", "oco"} and req.price is None:
        raise HTTPException(status_code=400, detail=f"price is required and must be > 0 for execution='{exec_norm}'.")
    if exec_norm in {"stop_limit", "stop_market", "oco"} and req.stop_price is None:
        raise HTTPException(status_code=400, detail=f"stop_price is required and must be > 0 for execution='{exec_norm}'.")
    if exec_norm == "oco" and req.stop_limit_price is None:
        raise HTTPException(status_code=400, detail="stop_limit_price is required and must be > 0 for execution='oco'.")

    client = get_nobitex_client()
    try:
        res = client.close_position(
            position_id=position_id,
            execution=exec_norm,
            amount=_format_decimal_str(req.amount),
            price=_format_decimal_str(req.price) if req.price is not None else None,
            stop_price=_format_decimal_str(req.stop_price) if req.stop_price is not None else None,
            stop_limit_price=_format_decimal_str(req.stop_limit_price) if req.stop_limit_price is not None else None,
        )
        try:
            updated = client.fetch_position_status(position_id)
            if updated:
                upsert_positions([updated])
        except Exception as st_err:
            logger.debug("Post-close position status refresh skipped for %s: %s", position_id, st_err)
        return {"status": "ok", "message": "سفارش بستن موقعیت ثبت شد.", "result": res}
    except Exception as e:
        return _map_trade_action_error(e)


@app.post("/api/positions/{position_id}/edit-collateral")
def api_edit_position_collateral(
    req: EditCollateralRequest,
    position_id: int = FastAPIPath(..., gt=0),
):
    """Increase or decrease collateral for an open margin position (requires TRADE permission)."""
    client = get_nobitex_client()
    try:
        res = client.edit_position_collateral(
            position_id=position_id,
            collateral=_format_decimal_str(req.collateral),
        )
        pos_obj = res.get("position") if isinstance(res, dict) else None
        if isinstance(pos_obj, dict) and pos_obj.get("id"):
            upsert_positions([pos_obj])
        else:
            try:
                updated = client.fetch_position_status(position_id)
                if updated:
                    upsert_positions([updated])
            except Exception as st_err:
                logger.debug("Post-collateral position refresh skipped for %s: %s", position_id, st_err)
        return {"status": "ok", "message": "وجه تضمین موقعیت با موفقیت به‌روزرسانی شد.", "result": res}
    except Exception as e:
        return _map_trade_action_error(e)


@app.post("/api/orders/cancel-old")
def api_cancel_old_orders(req: CancelOldOrdersRequest):
    """Bulk-cancel active orders matching filters via POST /market/orders/cancel-old (TRADE permission)."""
    if req.execution and req.execution.strip().lower() not in {"limit", "market", "stop_limit", "stop_market"}:
        raise HTTPException(status_code=400, detail="Invalid execution filter.")
    if req.trade_type and req.trade_type.strip().lower() not in {"spot", "margin"}:
        raise HTTPException(status_code=400, detail="Invalid trade_type filter. Must be 'spot' or 'margin'.")
    has_any_filter = any(
        val is not None and (not isinstance(val, str) or bool(val.strip()))
        for val in (req.hours, req.execution, req.trade_type, req.src_currency, req.dst_currency)
    )
    if not has_any_filter and not req.confirm_all:
        raise HTTPException(
            status_code=400,
            detail="At least one filter (hours, execution, trade_type, src_currency, dst_currency) or confirm_all=true is required.",
        )

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
async def api_ws_status():
    """Return diagnostic status of the backend Nobitex Centrifugo WebSocket bridge."""
    return get_ws_bridge().get_status()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Local WebSocket endpoint (ws://127.0.0.1:8080/ws) for Vue 3 dashboard tabs.
    Validates Origin header before accepting to prevent Cross-Site WebSocket Hijacking (CSWSH).
    """
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host")
    if origin is not None and not _is_allowed_local_origin(origin, host):
        logger.warning("Rejected cross-origin WebSocket attempt from origin=%r host=%r", origin, host)
        await websocket.close(code=1008)
        return

    await websocket.accept()
    bridge = get_ws_bridge()
    await bridge.register_client(websocket)
    try:
        while True:
            msg = await websocket.receive_json()
            await bridge.handle_client_message(websocket, msg)
    except WebSocketDisconnect:
        pass
    except Exception as ws_err:
        logger.debug("Local WS client loop closed: %s", ws_err)
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
