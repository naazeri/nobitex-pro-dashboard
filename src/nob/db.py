import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DATA_DIR / "portfolio.db"

EXCLUDED_MARKETS = {
    "USDT-RLS",
    "RLS-USDT",
    "USDT-IRT",
    "IRT-USDT",
    "DAI-RLS",
    "RLS-DAI",
    "DAI-IRT",
    "IRT-DAI",
    "DAI-USDT",
    "USDT-DAI",
}


def get_db_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[Path] = None):
    """Initialize SQLite tables and indexes."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY,
                order_id INTEGER,
                market TEXT NOT NULL,
                src_currency TEXT,
                dst_currency TEXT,
                type TEXT NOT NULL,
                price REAL NOT NULL,
                amount REAL NOT NULL,
                total REAL NOT NULL,
                fee REAL NOT NULL,
                timestamp TEXT NOT NULL,
                trade_type TEXT DEFAULT 'Spot',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Migration: ensure trade_type column exists
        cursor.execute("PRAGMA table_info(trades);")
        trade_cols = [c["name"] for c in cursor.fetchall()]
        if "trade_type" not in trade_cols:
            cursor.execute("ALTER TABLE trades ADD COLUMN trade_type TEXT DEFAULT 'Spot';")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_market ON trades(market);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_type ON trades(type);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_trade_type ON trades(trade_type);")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY,
                trade_type TEXT NOT NULL,
                market TEXT,
                type TEXT,
                execution TEXT,
                price REAL,
                amount REAL,
                total_price REAL,
                matched_amount REAL,
                unmatched_amount REAL,
                leverage REAL,
                status TEXT,
                created_at TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_trade_type ON orders(trade_type);")
        try:
            cursor.execute("ALTER TABLE orders ADD COLUMN unmatched_amount REAL DEFAULT 0;")
        except sqlite3.OperationalError:
            pass

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wallet_snapshots (
                currency TEXT PRIMARY KEY,
                balance REAL NOT NULL,
                active_balance REAL NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS market_prices (
                market TEXT PRIMARY KEY,
                latest REAL NOT NULL,
                day_change REAL,
                day_high REAL,
                day_low REAL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                side TEXT NOT NULL,
                src_currency TEXT NOT NULL,
                dst_currency TEXT NOT NULL,
                margin_type TEXT,
                leverage REAL NOT NULL,
                collateral REAL,
                entry_price REAL,
                exit_price REAL,
                liquidation_price REAL,
                mark_price REAL,
                pnl REAL,
                pnl_percent REAL,
                pool_pnl_share REAL,
                extension_fee REAL DEFAULT 0,
                opened_at TEXT,
                closed_at TEXT,
                created_at TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS btc_daily_history (
                date TEXT PRIMARY KEY,
                close_price REAL NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trade_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_type TEXT NOT NULL,
                target_id INTEGER NOT NULL,
                trade_id INTEGER,
                position_id INTEGER,
                notes TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                rating INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(entry_type, target_id)
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_journal_target ON trade_journal(entry_type, target_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_journal_trade ON trade_journal(trade_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_journal_pos ON trade_journal(position_id);")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chart_drawings (
                symbol TEXT PRIMARY KEY,
                overlays TEXT DEFAULT '[]',
                indicators TEXT DEFAULT '[]',
                timeframe TEXT DEFAULT '1D',
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Schema migrations
        try:
            cursor.execute("ALTER TABLE positions ADD COLUMN extension_fee REAL DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        conn.commit()


def upsert_orders(orders: List[Dict[str, Any]], db_path: Optional[Path] = None) -> int:
    """Insert or replace orders into database."""
    if not orders:
        return 0
    init_db(db_path)
    count = 0
    now_str = datetime.now().isoformat()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        for o in orders:
            order_id = o.get("id")
            if not order_id:
                continue
            trade_type = o.get("tradeType", "Spot")
            market = str(o.get("market", "")).upper()
            order_type = str(o.get("type", "")).lower()
            execution = o.get("execution", "")

            def to_float(val, default=0.0):
                if val is None:
                    return default
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return default

            price = to_float(o.get("price"))
            amount = to_float(o.get("amount"))
            total_price = to_float(o.get("totalPrice") or o.get("totalOrderPrice"))
            if total_price == 0.0 and price > 0 and amount > 0:
                total_price = price * amount
            matched_amount = to_float(o.get("matchedAmount"))
            unmatched_amount = to_float(o.get("unmatchedAmount"), max(0.0, amount - matched_amount))
            leverage = to_float(o.get("leverage"), 1.0)
            status = o.get("status", "")
            created_at = o.get("created_at")

            cursor.execute(
                """
                INSERT OR REPLACE INTO orders (
                    id, trade_type, market, type, execution, price,
                    amount, total_price, matched_amount, unmatched_amount, leverage, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    trade_type,
                    market,
                    order_type,
                    execution,
                    price,
                    amount,
                    total_price,
                    matched_amount,
                    unmatched_amount,
                    leverage,
                    status,
                    created_at,
                    now_str,
                ),
            )
            count += 1
        conn.commit()
    return count


def get_open_orders(trade_type: Optional[str] = None, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Retrieve active/open orders from database with optional trade_type filter ('Spot' | 'Margin')."""
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM orders WHERE status = 'Active'"
        params = []
        if trade_type:
            query += " AND trade_type = ?"
            params.append(trade_type)
        query += " ORDER BY created_at DESC"
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


def update_order_status(order_id: int, status: str, db_path: Optional[Path] = None) -> bool:
    """Update order status (e.g. to 'Canceled') in database."""
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        now_str = datetime.now().isoformat()
        cursor.execute(
            "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
            (status, now_str, order_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def upsert_trades(
    trades: List[Dict[str, Any]],
    order_type_map: Optional[Dict[int, str]] = None,
    db_path: Optional[Path] = None,
) -> int:
    """Insert or replace trades into database. Returns count of upserted records."""
    if not trades:
        return 0

    init_db(db_path)
    count = 0

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()

        if order_type_map is None:
            cursor.execute("SELECT id, trade_type FROM orders")
            order_type_map = {row["id"]: row["trade_type"] for row in cursor.fetchall()}

        for t in trades:
            trade_id = t.get("id")
            if not trade_id:
                continue

            order_id = t.get("orderId")
            market = str(t.get("market", "")).upper()
            src_curr = t.get("srcCurrency", "")
            dst_curr = t.get("dstCurrency", "")
            side = str(t.get("type", "")).lower()
            exec_trade_type = order_type_map.get(order_id, "Spot")

            try:
                price = float(t.get("price", 0.0))
            except (ValueError, TypeError):
                price = 0.0

            try:
                amount = float(t.get("amount", 0.0))
            except (ValueError, TypeError):
                amount = 0.0

            try:
                total = float(t.get("total", 0.0))
            except (ValueError, TypeError):
                total = 0.0

            try:
                fee = float(t.get("fee", 0.0))
            except (ValueError, TypeError):
                fee = 0.0

            timestamp = str(t.get("timestamp", ""))

            cursor.execute(
                """
                INSERT OR REPLACE INTO trades (
                    id, order_id, market, src_currency, dst_currency,
                    type, price, amount, total, fee, timestamp, trade_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_id,
                    order_id,
                    market,
                    src_curr,
                    dst_curr,
                    side,
                    price,
                    amount,
                    total,
                    fee,
                    timestamp,
                    exec_trade_type,
                ),
            )
            count += 1

        # Record last sync time
        now_str = datetime.now().isoformat()
        cursor.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('last_sync', ?)",
            (now_str,),
        )
        conn.commit()

    return count


def get_trades(
    exclude_fiat_stable: bool = True,
    trade_type: Optional[str] = None,
    execution_type: Optional[str] = None,
    market: Optional[str] = None,
    search: Optional[str] = None,
    tag: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    sort_by: str = "timestamp",
    sort_desc: bool = True,
    db_path: Optional[Path] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Retrieve trades matching filters with pagination and total count.
    """
    init_db(db_path)

    conditions = []
    params: List[Any] = []

    if exclude_fiat_stable:
        placeholders = ",".join("?" for _ in EXCLUDED_MARKETS)
        conditions.append(f"market NOT IN ({placeholders})")
        params.extend(list(EXCLUDED_MARKETS))

    if trade_type:
        conditions.append("type = ?")
        params.append(trade_type.lower())

    if execution_type and execution_type.lower() in {"spot", "margin"}:
        conditions.append("LOWER(trade_type) = ?")
        params.append(execution_type.lower())

    if market and market != "ALL":
        conditions.append("market = ?")
        params.append(market.upper())

    if search:
        s = f"%{search.strip().lower()}%"
        conditions.append("(CAST(id AS TEXT) LIKE ? OR CAST(order_id AS TEXT) LIKE ? OR LOWER(market) LIKE ?)")
        params.extend([s, s, s])

    if tag and tag.strip():
        tag_val = f'%"{tag.strip()}"%'
        conditions.append(
            "EXISTS (SELECT 1 FROM trade_journal tj WHERE tj.entry_type = 'trade' AND tj.target_id = trades.id AND tj.tags LIKE ?)"
        )
        params.append(tag_val)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    # Validate sort columns to avoid SQL injection (supports multi-column sort)
    valid_sort_cols = {
        "id",
        "order_id",
        "market",
        "type",
        "price",
        "amount",
        "total",
        "fee",
        "timestamp",
        "trade_type",
    }
    order_parts = []

    if sort_by:
        for part in str(sort_by).split(","):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                col_name, dir_name = part.split(":", 1)
                col_name = col_name.strip().lower()
                direction = "DESC" if dir_name.strip().lower() == "desc" else "ASC"
            else:
                col_name = part.lower()
                direction = "DESC" if sort_desc else "ASC"

            if col_name in valid_sort_cols:
                order_parts.append(f"{col_name} {direction}")

    if not order_parts:
        order_parts.append("timestamp DESC")

    order_clause = f"ORDER BY {', '.join(order_parts)}"

    offset = (max(1, page) - 1) * page_size

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()

        # Count query
        count_sql = f"SELECT COUNT(*) FROM trades {where_clause}"
        cursor.execute(count_sql, params)
        total_count = cursor.fetchone()[0]

        # Data query
        data_sql = f"SELECT * FROM trades {where_clause} {order_clause} LIMIT ? OFFSET ?"
        cursor.execute(data_sql, params + [page_size, offset])
        rows = [dict(row) for row in cursor.fetchall()]

        # Attach journal entry to each trade if present
        trade_ids = [r["id"] for r in rows]
        if trade_ids:
            placeholders_id = ",".join("?" for _ in trade_ids)
            cursor.execute(
                f"SELECT target_id, notes, tags, rating, updated_at FROM trade_journal WHERE entry_type = 'trade' AND target_id IN ({placeholders_id})",
                trade_ids,
            )
            journal_map = {}
            for jrow in cursor.fetchall():
                jd = dict(jrow)
                try:
                    jd["tags"] = json.loads(jd.get("tags") or "[]")
                except Exception:
                    jd["tags"] = []
                journal_map[jd["target_id"]] = jd

            for r in rows:
                r["journal"] = journal_map.get(r["id"])

    return rows, total_count


def get_distinct_markets(db_path: Optional[Path] = None) -> List[str]:
    """Get list of all distinct markets currently recorded in database."""
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT market FROM trades ORDER BY market ASC")
        return [row[0] for row in cursor.fetchall()]


def get_stats(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Get aggregate statistics for the dashboard cards."""
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM trades")
        total_trades = cursor.fetchone()[0]

        placeholders = ",".join("?" for _ in EXCLUDED_MARKETS)
        cursor.execute(f"SELECT COUNT(*) FROM trades WHERE market NOT IN ({placeholders})", list(EXCLUDED_MARKETS))
        crypto_trades = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM trades WHERE type = 'buy'")
        buy_trades = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM trades WHERE type = 'sell'")
        sell_trades = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT market) FROM trades")
        active_markets = cursor.fetchone()[0]

        cursor.execute("SELECT MAX(timestamp) FROM trades")
        latest_trade_time = cursor.fetchone()[0]

        cursor.execute("SELECT value FROM metadata WHERE key = 'last_sync'")
        row = cursor.fetchone()
        last_sync = row[0] if row else None

    return {
        "total_trades": total_trades,
        "crypto_trades": crypto_trades,
        "fiat_stable_trades": total_trades - crypto_trades,
        "buy_trades": buy_trades,
        "sell_trades": sell_trades,
        "active_markets_count": active_markets,
        "last_sync": last_sync,
        "latest_trade_time": latest_trade_time,
    }


def upsert_wallet_snapshots(wallets: List[Dict[str, Any]], db_path: Optional[Path] = None) -> int:
    """Store wallet balances in SQLite."""
    if not wallets:
        return 0
    init_db(db_path)
    count = 0
    now_str = datetime.now().isoformat()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        for w in wallets:
            curr = str(w.get("currency", "")).upper()
            if not curr:
                continue
            try:
                bal = float(w.get("balance", 0.0))
                active_bal = float(w.get("activeBalance", bal))
            except (ValueError, TypeError):
                bal = 0.0
                active_bal = 0.0
            cursor.execute(
                """
                INSERT OR REPLACE INTO wallet_snapshots (currency, balance, active_balance, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (curr, bal, active_bal, now_str),
            )
            count += 1
        conn.commit()
    return count


def get_wallet_snapshots(db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Retrieve saved wallet balances."""
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT currency, balance, active_balance, updated_at FROM wallet_snapshots")
        return [dict(row) for row in cursor.fetchall()]


def upsert_market_prices(prices: Dict[str, Any], db_path: Optional[Path] = None) -> int:
    """Store market prices in SQLite."""
    if not prices:
        return 0
    init_db(db_path)
    count = 0
    now_str = datetime.now().isoformat()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        for market, data in prices.items():
            if not isinstance(data, dict):
                continue
            try:
                latest = float(data.get("latest", 0.0))
                day_change = float(data.get("dayChange", 0.0))
                day_high = float(data.get("dayHigh", 0.0))
                day_low = float(data.get("dayLow", 0.0))
            except (ValueError, TypeError):
                continue
            cursor.execute(
                """
                INSERT OR REPLACE INTO market_prices (market, latest, day_change, day_high, day_low, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (market.lower(), latest, day_change, day_high, day_low, now_str),
            )
            count += 1
        conn.commit()
    return count


def get_market_prices(db_path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Retrieve stored market prices as a dictionary."""
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT market, latest, day_change, day_high, day_low, updated_at FROM market_prices")
        prices = {}
        for row in cursor.fetchall():
            m = row["market"].lower()
            prices[m] = {
                "latest": row["latest"],
                "dayChange": row["day_change"],
                "dayHigh": row["day_high"],
                "dayLow": row["day_low"],
                "updated_at": row["updated_at"],
            }
        return prices


def get_all_crypto_trades_chronological(
    since_date: Optional[str] = None,
    trade_type: Optional[str] = "Spot",
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Retrieve all non-fiat-stable trades ordered chronologically for PnL calculation."""
    init_db(db_path)
    placeholders = ",".join("?" for _ in EXCLUDED_MARKETS)
    conditions = [f"market NOT IN ({placeholders})"]
    params: List[Any] = list(EXCLUDED_MARKETS)

    if trade_type:
        conditions.append("trade_type = ?")
        params.append(trade_type)

    if since_date:
        conditions.append("timestamp >= ?")
        params.append(since_date)

    sql = f"""
        SELECT * FROM trades 
        WHERE {' AND '.join(conditions)}
        ORDER BY timestamp ASC
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]


def get_margin_trades(db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Retrieve all margin trades from database."""
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades WHERE trade_type = 'Margin' ORDER BY timestamp ASC")
        return [dict(row) for row in cursor.fetchall()]


def upsert_positions(positions: List[Dict[str, Any]], db_path: Optional[Path] = None) -> int:
    """Store or update margin/leverage positions in SQLite."""
    if not positions:
        return 0
    init_db(db_path)
    count = 0
    now_str = datetime.now().isoformat()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        for p in positions:
            pos_id = p.get("id")
            if not pos_id:
                continue

            status = str(p.get("status", "")).strip()
            side = str(p.get("side", "")).strip().lower()
            src_curr = str(p.get("srcCurrency", "")).strip().upper()
            dst_curr = str(p.get("dstCurrency", "")).strip().upper()
            margin_type = str(p.get("marginType", ""))

            def to_float(val, default=0.0):
                if val is None:
                    return default
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return default

            leverage = to_float(p.get("leverage"), 1.0)
            collateral = to_float(p.get("collateral"), 0.0)
            entry_price = to_float(p.get("entryPrice"), 0.0)
            exit_price = to_float(p.get("exitPrice"), 0.0)
            liq_price = to_float(p.get("liquidationPrice"), 0.0)
            mark_price = to_float(p.get("markPrice"), 0.0)
            pnl = to_float(p.get("PNL", p.get("unrealizedPNL")), 0.0)
            pnl_pct = to_float(p.get("PNLPercent", p.get("unrealizedPNLPercent")), 0.0)
            pool_share = to_float(p.get("poolPNLShare"), 0.0)
            extension_fee = to_float(p.get("extensionFee", p.get("extension_fee")), 0.0)

            opened_at = p.get("openedAt")
            closed_at = p.get("closedAt")
            created_at = p.get("createdAt")

            cursor.execute(
                """
                INSERT OR REPLACE INTO positions (
                    id, status, side, src_currency, dst_currency, margin_type,
                    leverage, collateral, entry_price, exit_price, liquidation_price,
                    mark_price, pnl, pnl_percent, pool_pnl_share, extension_fee, opened_at,
                    closed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pos_id,
                    status,
                    side,
                    src_curr,
                    dst_curr,
                    margin_type,
                    leverage,
                    collateral,
                    entry_price,
                    exit_price,
                    liq_price,
                    mark_price,
                    pnl,
                    pnl_pct,
                    pool_share,
                    extension_fee,
                    opened_at,
                    closed_at,
                    created_at,
                    now_str,
                ),
            )
            count += 1
        conn.commit()
    return count


def get_positions(
    status: Optional[str] = None, db_path: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """Retrieve positions from SQLite. status can be 'active' (Open) or 'past' (Closed/Liquidated)."""
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        if status == "active" or status == "Open":
            cursor.execute(
                "SELECT * FROM positions WHERE status = 'Open' ORDER BY opened_at DESC"
            )
        elif status == "past":
            cursor.execute(
                "SELECT * FROM positions WHERE status != 'Open' ORDER BY closed_at DESC, opened_at DESC"
            )
        else:
            cursor.execute("SELECT * FROM positions ORDER BY opened_at DESC")
        rows = [dict(row) for row in cursor.fetchall()]

        pos_ids = [r["id"] for r in rows]
        if pos_ids:
            placeholders_pos = ",".join("?" for _ in pos_ids)
            cursor.execute(
                f"SELECT target_id, notes, tags, rating, updated_at FROM trade_journal WHERE entry_type = 'position' AND target_id IN ({placeholders_pos})",
                pos_ids,
            )
            journal_map = {}
            for jrow in cursor.fetchall():
                jd = dict(jrow)
                try:
                    jd["tags"] = json.loads(jd.get("tags") or "[]")
                except Exception:
                    jd["tags"] = []
                journal_map[jd["target_id"]] = jd

            for r in rows:
                r["journal"] = journal_map.get(r["id"])

        return rows


def upsert_btc_daily_history(
    records: List[Dict[str, Any]], db_path: Optional[Path] = None
) -> int:
    """Insert or replace BTC daily close prices into database."""
    if not records:
        return 0
    init_db(db_path)
    count = 0
    now_str = datetime.now().isoformat()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        for r in records:
            date_str = str(r.get("date", ""))
            try:
                close_price = float(r.get("close_price", 0.0))
            except (ValueError, TypeError):
                continue
            if not date_str or close_price <= 0:
                continue
            cursor.execute(
                """
                INSERT INTO btc_daily_history (date, close_price, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    close_price=excluded.close_price,
                    updated_at=excluded.updated_at
                """,
                (date_str, close_price, now_str),
            )
            count += 1
        conn.commit()
    return count


def get_btc_daily_history(
    since_date: Optional[str] = None,
    until_date: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, float]:
    """Retrieve cached BTC daily close prices mapped as { 'YYYY-MM-DD': close_price }."""
    init_db(db_path)
    query = "SELECT date, close_price FROM btc_daily_history WHERE 1=1"
    params = []
    if since_date:
        query += " AND date >= ?"
        params.append(since_date)
    if until_date:
        query += " AND date <= ?"
        params.append(until_date)
    query += " ORDER BY date ASC"

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return {row["date"]: float(row["close_price"]) for row in cursor.fetchall()}


def upsert_journal_entry(
    entry_type: str,
    target_id: int,
    notes: str = "",
    tags: Optional[List[str]] = None,
    rating: int = 0,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Insert or update a journal entry for a trade or position."""
    init_db(db_path)
    if tags is None:
        tags = []
    tags_json = json.dumps(tags, ensure_ascii=False)
    now_str = datetime.now().isoformat()
    trade_id = target_id if entry_type == "trade" else None
    position_id = target_id if entry_type == "position" else None

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO trade_journal (entry_type, target_id, trade_id, position_id, notes, tags, rating, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entry_type, target_id) DO UPDATE SET
                notes = excluded.notes,
                tags = excluded.tags,
                rating = excluded.rating,
                trade_id = excluded.trade_id,
                position_id = excluded.position_id,
                updated_at = excluded.updated_at
            """,
            (entry_type, target_id, trade_id, position_id, notes, tags_json, rating, now_str, now_str),
        )
        conn.commit()

        cursor.execute(
            "SELECT * FROM trade_journal WHERE entry_type = ? AND target_id = ?",
            (entry_type, target_id),
        )
        row = cursor.fetchone()
        if row:
            res = dict(row)
            try:
                res["tags"] = json.loads(res.get("tags") or "[]")
            except Exception:
                res["tags"] = []
            return res
        return {}


def get_journal_entry(
    entry_type: str, target_id: int, db_path: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    """Retrieve a single journal entry for a trade or position."""
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM trade_journal WHERE entry_type = ? AND target_id = ?",
            (entry_type, target_id),
        )
        row = cursor.fetchone()
        if not row:
            return None
        res = dict(row)
        try:
            res["tags"] = json.loads(res.get("tags") or "[]")
        except Exception:
            res["tags"] = []
        return res


def get_all_journal_entries(
    entry_type: Optional[str] = None, db_path: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """Retrieve all journal entries, optionally filtered by entry_type."""
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        if entry_type:
            cursor.execute(
                "SELECT * FROM trade_journal WHERE entry_type = ? ORDER BY updated_at DESC",
                (entry_type,),
            )
        else:
            cursor.execute("SELECT * FROM trade_journal ORDER BY updated_at DESC")
        entries = []
        for row in cursor.fetchall():
            d = dict(row)
            try:
                d["tags"] = json.loads(d.get("tags") or "[]")
            except Exception:
                d["tags"] = []
            entries.append(d)
        return entries


def delete_journal_entry(
    entry_type: str, target_id: int, db_path: Optional[Path] = None
) -> bool:
    """Delete a journal entry."""
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM trade_journal WHERE entry_type = ? AND target_id = ?",
            (entry_type, target_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def get_journal_tags_summary(db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Aggregate tags usage across all journal entries."""
    entries = get_all_journal_entries(db_path=db_path)
    tag_counts: Dict[str, int] = {}
    for e in entries:
        for t in e.get("tags", []):
            if t:
                tag_counts[t] = tag_counts.get(t, 0) + 1

    summary = [{"tag": k, "count": v} for k, v in tag_counts.items()]
    summary.sort(key=lambda x: x["count"], reverse=True)
    return summary


def get_chart_state(symbol: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Retrieve saved overlays, indicators, and timeframe for a symbol."""
    init_db(db_path)
    clean_symbol = symbol.strip().upper()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT symbol, overlays, indicators, timeframe, updated_at FROM chart_drawings WHERE symbol = ?",
            (clean_symbol,),
        )
        row = cursor.fetchone()
        if not row:
            return {
                "symbol": clean_symbol,
                "overlays": [],
                "indicators": ["VOL"],
                "timeframe": "1D",
                "updated_at": None,
            }

        try:
            overlays = json.loads(row["overlays"] or "[]")
        except Exception:
            overlays = []
        try:
            indicators = json.loads(row["indicators"] or "[]")
        except Exception:
            indicators = ["VOL"]

        return {
            "symbol": row["symbol"],
            "overlays": overlays,
            "indicators": indicators,
            "timeframe": row["timeframe"] or "1D",
            "updated_at": row["updated_at"],
        }


def save_chart_state(
    symbol: str,
    overlays: Optional[List[Dict[str, Any]]] = None,
    indicators: Optional[List[str]] = None,
    timeframe: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> bool:
    """Save or update chart overlays, active indicators, and timeframe for a symbol."""
    init_db(db_path)
    clean_symbol = symbol.strip().upper()
    overlays_json = json.dumps(overlays or [])
    indicators_json = json.dumps(indicators or ["VOL"])
    tf = timeframe or "1D"
    now_str = datetime.now().isoformat()

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO chart_drawings (symbol, overlays, indicators, timeframe, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                overlays = excluded.overlays,
                indicators = excluded.indicators,
                timeframe = excluded.timeframe,
                updated_at = excluded.updated_at
            """,
            (clean_symbol, overlays_json, indicators_json, tf, now_str),
        )
        conn.commit()
        return True




