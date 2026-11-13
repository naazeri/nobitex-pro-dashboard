import io
import csv
import webbrowser
from pathlib import Path
from typing import Optional
from datetime import datetime

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
def api_get_portfolio(since: Optional[str] = "2026-10-17"):
    """
    Return spot portfolio analytics in USDT using Weighted Average Cost (WAC).
    since: '2026-10-17' for active cycle (default), or 'all' for all valid history.
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

    since_date = None if since == "all" else since
    trades = get_all_crypto_trades_chronological(since_date=since_date)
    portfolio = calculate_portfolio(trades, wallets, prices, since_date=since_date)
    return portfolio


@app.get("/api/portfolio/charts")
def api_get_portfolio_charts(since: Optional[str] = "2026-10-17"):
    """Return chart data for Asset Allocation, PnL by Asset, and Timeline."""
    portfolio = api_get_portfolio(since=since)
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
def api_get_pnl_calendar(since: Optional[str] = "all"):
    """
    Return daily PnL calendar breakdown and month statistics.
    since: '2026-10-17' for active cycle or 'all' for all valid history.
    """
    since_date = None if since == "all" else since
    trades = get_all_crypto_trades_chronological(since_date=None)
    positions = get_positions(status="past")
    prices = get_market_prices()
    return calculate_daily_pnl_calendar(trades, positions, prices, since_date=since_date)


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
    timeframe: str = "cycle",  # '7d', '30d', '90d', 'cycle', 'all'
    since: Optional[str] = "2026-10-17",
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
    positions = get_positions(status="past")
    wallets = get_wallet_snapshots()
    prices = get_market_prices()

    return calculate_equity_curve(
        trades=trades,
        past_positions=positions,
        wallets=wallets,
        market_prices=prices,
        btc_prices_map=btc_cache,
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

        if lang == "fa" and jdatetime:
            raw_ts = str(row.get("timestamp", ""))
            try:
                clean_ts = raw_ts.replace("Z", "+00:00")
                dt = datetime.fromisoformat(clean_ts)
                jdt = jdatetime.datetime.fromgregorian(datetime=dt)
                row["timestamp"] = jdt.strftime("%Y/%m/%d %H:%M:%S")
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


# Serve index.html
@app.get("/", response_class=HTMLResponse)
def serve_index():
    index_file = STATIC_DIR / "index.html"
    if not index_file.is_file():
        raise HTTPException(status_code=404, detail="Frontend file not found.")
    return index_file.read_text(encoding="utf-8")


# Mount static directory
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def main():
    print("🚀 Starting Nobitex Portfolio Web Server at http://127.0.0.1:8080 ...")
    webbrowser.open("http://127.0.0.1:8080")
    uvicorn.run("nob.server:app", host="127.0.0.1", port=8080, reload=False)


if __name__ == "__main__":
    main()
