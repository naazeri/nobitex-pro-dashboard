import io
import csv
import time
import urllib.request
import json
import webbrowser
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import zoneinfo

TEHRAN_TZ = zoneinfo.ZoneInfo("Asia/Tehran")

import uvicorn
from fastapi import FastAPI, HTTPException, BackgroundTasks, Response
from fastapi.responses import HTMLResponse
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
    update_order_status,
)
from nob.nobitex_client import NobitexClient
from nob.pnl import (
    calculate_portfolio,
    calculate_positions_summary,
    calculate_daily_pnl_calendar,
    calculate_equity_curve,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Nobitex Portfolio & Trades", version="2.1.0")

# Global sync status
sync_status = {
    "is_syncing": False,
    "last_result": None,
    "last_error": None,
    "step": None,
}


@app.on_event("startup")
def startup_event():
    init_db()


@app.get("/api/stats")
def api_get_stats():
    """Return dashboard KPI stats."""
    return get_stats()


@app.get("/api/markets")
def api_get_markets():
    """Return distinct active markets and detailed pricing/leverage for calculator."""
    markets = get_distinct_markets()
    prices = get_market_prices()
    
    leverage_map = {
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
        max_lev = leverage_map.get(src, 5)
        
        details[m] = {
            "market": m,
            "src_currency": src,
            "dst_currency": dst,
            "latest": latest_price,
            "max_leverage": max_lev,
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
            client = NobitexClient()
            fresh_prices = client.fetch_market_stats()
            upsert_market_prices(fresh_prices)
            prices = fresh_prices
        except Exception:
            pass

    effective_from = from_date or (since if since and since != "all" else None)
    trades = get_all_crypto_trades_chronological(from_date=effective_from, to_date=to_date)
    portfolio = calculate_portfolio(trades, wallets, prices, from_date=effective_from, to_date=to_date)
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

    # If no positions saved yet, fetch directly from Nobitex
    if not positions:
        try:
            client = NobitexClient()
            active = client.fetch_positions("active", page_size=100)
            past = client.fetch_positions("past", page_size=100)
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
    Fetches daily candles from Nobitex UDF API if needed.
    """
    try:
        import urllib.request
        import json
        import time

        now = int(time.time())
        from_ts = now - (365 * 86400)
        url = f"https://apiv2.nobitex.ir/market/udf/history?symbol=BTCUSDT&resolution=D&from={from_ts}&to={now}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if data.get("s") == "ok":
                timestamps = data.get("t", [])
                closes = data.get("c", [])
                records = []
                for ts, c in zip(timestamps, closes):
                    d_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
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
):
    """
    Return continuous daily equity curve, cumulative PnL, and Bitcoin benchmark comparison.
    """
    try:
        btc_cache = get_btc_daily_history()
        today_str = datetime.now().strftime("%Y-%m-%d")
        if not btc_cache or today_str not in btc_cache:
            sync_btc_history_if_needed()
            btc_cache = get_btc_daily_history()
    except Exception:
        btc_cache = get_btc_daily_history()

    trades = get_all_crypto_trades_chronological(since_date=None)
    raw_positions = get_positions(status="past")
    wallets = get_wallet_snapshots()
    prices = get_market_prices()
    positions = calculate_positions_summary(raw_positions, prices).get("past", [])

    return calculate_equity_curve(
        trades=trades,
        past_positions=positions,
        wallets=wallets,
        market_prices=prices,
        btc_prices_map=btc_cache,
        from_date=from_date,
        to_date=to_date,
        since_date=since,
        timeframe=timeframe,
    )


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


def perform_sync():
    global sync_status
    try:
        sync_status["is_syncing"] = True
        sync_status["last_error"] = None
        sync_status["step"] = "Connecting to Nobitex..."

        client = NobitexClient()

        # 1. Sync wallet balances
        sync_status["step"] = "Fetching wallet balances..."
        try:
            wallets = client.fetch_wallets()
            upsert_wallet_snapshots(wallets)
        except Exception as e:
            print("Notice: Wallet fetch failed:", e)

        # 2. Sync real-time market prices (public)
        sync_status["step"] = "Updating market prices..."
        try:
            prices = client.fetch_market_stats()
            upsert_market_prices(prices)
        except Exception as e:
            print("Notice: Market prices fetch failed:", e)

        # 3. Sync orders to classify tradeType (Spot vs Margin)
        sync_status["step"] = "Fetching user orders..."
        order_type_map = {}
        try:
            orders = client.fetch_all_orders(status="all", page_size=100)
            upsert_orders(orders)
            order_type_map = {o["id"]: o.get("tradeType", "Spot") for o in orders}
        except Exception as e:
            print("Notice: Orders fetch failed:", e)

        # 4. Sync margin & futures positions
        sync_status["step"] = "Fetching margin positions..."
        pos_count = 0
        try:
            active_pos = client.fetch_positions("active", page_size=100)
            past_pos = client.fetch_positions("past", page_size=100)
            pos_count = upsert_positions(active_pos + past_pos)
        except Exception as e:
            print("Notice: Positions fetch failed:", e)

        # 5. Sync trades
        sync_status["step"] = "Syncing trade history..."
        trades = client.fetch_all_trades(page_size=500)
        saved_count = upsert_trades(trades, order_type_map=order_type_map)

        sync_status["last_result"] = (
            f"Successfully synced {saved_count} trades, {pos_count} positions, and updated wallets."
        )
    except Exception as e:
        sync_status["last_error"] = str(e)
    finally:
        sync_status["is_syncing"] = False
        sync_status["step"] = None


@app.post("/api/sync")
def api_sync_trades(background_tasks: BackgroundTasks):
    """Trigger background sync with Nobitex API."""
    global sync_status
    if sync_status["is_syncing"]:
        return {"status": "in_progress", "message": "Sync is already in progress."}

    sync_status["is_syncing"] = True
    background_tasks.add_task(perform_sync)
    return {"status": "started", "message": "Synchronization started."}


@app.get("/api/sync/status")
def api_sync_status():
    """Check sync progress status."""
    return sync_status


@app.post("/api/prices/refresh")
def api_refresh_prices():
    """
    Fetch real-time market stats from Nobitex public API and update SQLite.
    Returns the refreshed prices count and timestamp.
    """
    try:
        client = NobitexClient()
        fresh_prices = client.fetch_market_stats()
        count = upsert_market_prices(fresh_prices)
        return {
            "status": "ok",
            "updated_count": count,
            "timestamp": datetime.now().isoformat(),
        }
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


# In-memory candle cache: key -> (expire_time, data)
_candle_cache: Dict[str, Tuple[float, Any]] = {}


@app.get("/api/chart/candles")
def api_get_candles(
    symbol: str = "BTCUSDT",
    resolution: str = "D",
    from_ts: Optional[int] = None,
    to_ts: Optional[int] = None,
):
    """
    Fetch OHLCV candles from Nobitex UDF API with in-memory caching and weekly aggregation.
    Supports resolutions: 15, 30, 60 (1H), 240 (4H), D (1D), 1W (Weekly).
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

    if from_ts is None:
        if res_norm == "1W":
            from_ts = to_ts - (5 * 365 * 86400)  # 5 years for weekly
        elif res_norm == "D":
            from_ts = to_ts - (365 * 86400)  # 1 year
        elif res_norm in ["60", "240"]:
            from_ts = to_ts - (60 * 86400)   # 60 days
        else:
            from_ts = to_ts - (14 * 86400)   # 14 days

    cache_key = f"{clean_symbol}:{res_norm}:{from_ts // 60}:{to_ts // 60}"
    cached = _candle_cache.get(cache_key)
    if cached and cached[0] > time.time():
        return cached[1]

    fetch_res = "D" if res_norm == "1W" else res_norm
    fetch_from = from_ts - (7 * 86400) if res_norm == "1W" else from_ts

    url = f"https://apiv2.nobitex.ir/market/udf/history?symbol={clean_symbol}&resolution={fetch_res}&from={fetch_from}&to={to_ts}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return {
                "status": "restricted",
                "symbol": clean_symbol,
                "message": "Access restricted by exchange (HTTP 403 Forbidden)",
                "candles": [],
            }
        raise HTTPException(status_code=502, detail=f"Nobitex UDF Error: HTTP {e.code}")
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

    _candle_cache[cache_key] = (time.time() + 60, result)
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

    client = NobitexClient()
    raw_orders = []
    source = "live"
    try:
        raw_orders = client.fetch_all_orders(status="open", page_size=100)
        if raw_orders:
            upsert_orders(raw_orders)
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
        "updated_at": datetime.now().isoformat(),
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

    client = NobitexClient()
    try:
        res = client.cancel_order(req.order_id)
        update_order_status(req.order_id, "Canceled")
        _open_orders_cache = {"timestamp": 0.0, "data": None}
        return {"status": "ok", "message": "سفارش با موفقیت لغو شد.", "result": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
