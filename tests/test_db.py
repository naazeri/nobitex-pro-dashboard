from __future__ import annotations

import sqlite3
from pathlib import Path

from nob import db


def test_get_db_connection_always_closes_on_success_and_error(isolated_db: Path):
    with db.get_db_connection(isolated_db) as conn:
        conn.execute("SELECT 1").fetchone()
    # Accessing a closed connection raises ProgrammingError
    try:
        conn.execute("SELECT 1")
        assert False, "Connection should be closed after exiting with-block"
    except sqlite3.ProgrammingError:
        pass

    try:
        with db.get_db_connection(isolated_db) as conn2:
            conn2.execute("INSERT INTO metadata(key, value) VALUES ('k_fail', 'v')")
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    try:
        conn2.execute("SELECT 1")
        assert False, "Connection should be closed after exception"
    except sqlite3.ProgrammingError:
        pass

    # Verify rollback occurred
    with db.get_db_connection(isolated_db) as conn3:
        row = conn3.execute("SELECT value FROM metadata WHERE key = 'k_fail'").fetchone()
        assert row is None


def test_secondary_indexes_and_wal_mode_created(isolated_db: Path):
    with db.get_db_connection(isolated_db) as conn:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert str(mode).lower() == "wal"
        indexes = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
            ).fetchall()
        }
    expected = {
        "idx_trades_order_id",
        "idx_trades_trade_type_ts",
        "idx_orders_status_created",
        "idx_positions_status_opened",
        "idx_positions_status_closed",
        "idx_cashflows_type_ts",
    }
    assert expected.issubset(indexes)


def test_atomic_reconcile_open_orders_handles_large_batches(isolated_db: Path):
    # Seed 600 active orders (exceeds 500 chunk size) + 2 stale ones
    initial_orders = [
        {
            "id": i,
            "tradeType": "Spot",
            "market": "BTC-USDT",
            "type": "buy",
            "execution": "limit",
            "price": 50000,
            "amount": 0.01,
            "status": "Active",
            "created_at": "2026-10-01T00:00:00+00:00",
        }
        for i in range(1, 603)
    ]
    db.upsert_orders(initial_orders, db_path=isolated_db)

    live_snapshot = initial_orders[:600]  # 601 and 602 disappeared
    closed = db.reconcile_open_orders(live_snapshot, db_path=isolated_db)
    assert closed == 2
    remaining_open = db.get_open_orders(db_path=isolated_db)
    assert len(remaining_open) == 600


def test_upsert_trades_scoped_order_lookup_and_margin_backfill(isolated_db: Path):
    db.upsert_trades(
        [
            {
                "id": 1001,
                "orderId": 5001,
                "market": "BTC-USDT",
                "srcCurrency": "btc",
                "dstCurrency": "usdt",
                "type": "buy",
                "price": 60000,
                "amount": 0.01,
                "total": 600,
                "fee": 0.00001,
                "timestamp": "2026-10-01T10:00:00+00:00",
            },
            {
                "id": 1002,
                "orderId": 5002,
                "market": "USDT-RLS",
                "srcCurrency": "usdt",
                "dstCurrency": "rls",
                "type": "buy",
                "price": 1000000,
                "amount": 100,
                "total": 100000000,
                "fee": 0.1,
                "timestamp": "2026-10-02T10:00:00+00:00",
            },
        ],
        db_path=isolated_db,
    )
    # Order 5002 arrives later as Margin -> must backfill trade 1002
    db.upsert_orders(
        [
            {
                "id": 5002,
                "tradeType": "Margin",
                "market": "USDT-RLS",
                "type": "buy",
                "status": "Done",
                "price": 1000000,
                "amount": 100,
            }
        ],
        db_path=isolated_db,
    )
    rows, total = db.get_trades(
        exclude_fiat_stable=False, execution_type="margin", db_path=isolated_db
    )
    assert total == 1
    assert rows[0]["id"] == 1002
    assert rows[0]["trade_type"] == "Margin"


def test_get_trades_like_escaping_and_single_query_stats(isolated_db: Path):
    db.upsert_trades(
        [
            {
                "id": 1,
                "orderId": 10,
                "market": "1K_SHIB-USDT",
                "type": "buy",
                "price": 0.02,
                "amount": 100,
                "total": 2,
                "fee": 0.1,
                "timestamp": "2026-10-01T10:00:00+00:00",
            },
            {
                "id": 2,
                "orderId": 11,
                "market": "1KXSHIB-USDT",
                "type": "sell",
                "price": 0.02,
                "amount": 100,
                "total": 2,
                "fee": 0.002,
                "timestamp": "2026-10-02T10:00:00+00:00",
            },
            {
                "id": 3,
                "orderId": 12,
                "market": "USDT-RLS",
                "type": "buy",
                "price": 1000000,
                "amount": 10,
                "total": 10000000,
                "fee": 0.01,
                "timestamp": "2026-10-03T10:00:00+00:00",
            },
        ],
        db_path=isolated_db,
    )
    # Underscore in symbol must be matched literally, not as wildcard
    rows, total = db.get_trades(symbol="1K_SHIB", db_path=isolated_db)
    assert total == 1
    assert rows[0]["market"] == "1K_SHIB-USDT"

    stats = db.get_stats(db_path=isolated_db)
    assert stats["total_trades"] == 3
    assert stats["crypto_trades"] == 2
    assert stats["fiat_stable_trades"] == 1
    assert stats["buy_trades"] == 2
    assert stats["sell_trades"] == 1
    assert stats["active_markets_count"] == 3
    assert stats["latest_trade_time"] == "2026-10-03T10:00:00+00:00"


def test_metadata_cache_handles_naive_and_aware_timestamps(isolated_db: Path):
    db.set_metadata_json("naive_key", {"ok": True}, db_path=isolated_db)
    data, stale = db.get_metadata_cache("naive_key", ttl_seconds=3600, db_path=isolated_db)
    assert data == {"ok": True}
    assert stale is False

    # Manually insert a naive ISO timestamp to verify it does not crash
    with db.get_db_connection(isolated_db) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES ('naive_ts', ?)",
            ('{"data": {"x": 1}, "updated_at": "2020-01-01T00:00:00"}',),
        )
    data2, stale2 = db.get_metadata_cache("naive_ts", ttl_seconds=60, db_path=isolated_db)
    assert data2 == {"x": 1}
    assert stale2 is True
