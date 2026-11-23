from __future__ import annotations

from datetime import timedelta

from nob.pnl import (
    calculate_daily_pnl_calendar,
    calculate_equity_curve,
    calculate_portfolio,
    calculate_positions_summary,
)
from nob.time_utils import now_irst


def _prices() -> dict:
    return {
        "btc-usdt": {"latest": 60000.0, "day_change": 1.0, "day_high": 61000.0, "day_low": 59000.0},
        "bnb-usdt": {"latest": 550.0, "day_change": -0.5, "day_high": 560.0, "day_low": 540.0},
        "xaut-usdt": {"latest": 4000.0, "day_change": 0.2, "day_high": 4050.0, "day_low": 3950.0},
        "usdt-rls": {"latest": 1000000.0, "day_change": 0.0, "day_high": 1000000.0, "day_low": 1000000.0},
    }


def test_adr014_completely_unmatched_sell_is_ignored():
    trades = [
        {
            "id": 1,
            "market": "XAUT-USDT",
            "type": "sell",
            "price": 4000.0,
            "amount": 0.005,
            "total": 20.0,
            "fee": 0.02,
            "timestamp": "2026-08-14T10:00:00+00:00",
        }
    ]
    res = calculate_portfolio(trades, wallets=[], market_stats=_prices())
    assert res["summary"]["total_realized_pnl"] == 0.0
    assert res["summary"]["total_fees_usdt"] == 0.0
    assert res["summary"]["ignored_unmatched_sells_count"] == 1

    cal = calculate_daily_pnl_calendar(trades, [], _prices())
    assert cal["daily_stats"] == {}
    assert cal["summary"]["total_trades_count"] == 0


def test_adr014_partially_matched_sell_proration():
    trades = [
        {
            "id": 1,
            "market": "BTC-USDT",
            "type": "buy",
            "price": 50000.0,
            "amount": 1.0,
            "total": 50000.0,
            "fee": 0.0,
            "timestamp": "2026-06-01T10:00:00+00:00",
        },
        {
            "id": 2,
            "market": "BTC-USDT",
            "type": "sell",
            "price": 60000.0,
            "amount": 2.0,
            "total": 120000.0,
            "fee": 100.0,
            "timestamp": "2026-06-02T10:00:00+00:00",
        },
    ]
    res = calculate_portfolio(trades, wallets=[], market_stats=_prices())
    # Only 1.0 BTC matched out of 2.0 -> 50% fee ($50) -> realized PnL = 60000 - 50000 - 50 = 9950
    assert res["summary"]["total_realized_pnl"] == 9950.0
    btc = next(h for h in res["holdings"] if h["symbol"] == "BTC")
    assert btc["total_sold_qty"] == 1.0
    assert btc["fees_paid_usdt"] == 50.0


def test_adr014_crypto_withdrawal_wac_replay():
    # Buy 0.02 @ 600, withdraw 0.018 (leaving 0.002 @ 600), then buy 0.012 @ 736 -> avg ~716.57
    trades = [
        {
            "id": 1,
            "market": "BNB-USDT",
            "type": "buy",
            "price": 600.0,
            "amount": 0.02,
            "total": 12.0,
            "fee": 0.0,
            "timestamp": "2026-06-01T00:00:00+00:00",
        },
        {
            "id": 2,
            "market": "BNB-USDT",
            "type": "buy",
            "price": 736.0,
            "amount": 0.012,
            "total": 8.832,
            "fee": 0.0,
            "timestamp": "2026-08-01T00:00:00+00:00",
        },
    ]
    cashflows = [
        {
            "id": "withdraw:100",
            "flow_type": "withdraw",
            "currency": "BNB",
            "amount": 0.018,
            "status": "done",
            "timestamp": "2026-07-04T09:18:25+00:00",
        }
    ]
    wallets = [{"currency": "BNB", "wallet_type": "spot", "balance": 0.014, "active_balance": 0.014}]
    res = calculate_portfolio(trades, wallets=wallets, market_stats=_prices(), cashflows=cashflows)
    bnb = next(h for h in res["holdings"] if h["symbol"] == "BNB")
    # (0.002 * 600 + 8.832) / 0.014 = 10.032 / 0.014 = 716.5714...
    assert abs(bnb["avg_cost"] - 716.5714) < 0.01


def test_adr002_net_amount_and_partial_history_protection():
    trades = [
        {
            "id": 1,
            "market": "BTC-USDT",
            "type": "buy",
            "price": 50000.0,
            "amount": 0.1,
            "total": 5000.0,
            "fee": 0.001,  # net_amount = 0.099
            "timestamp": "2026-06-01T00:00:00+00:00",
        }
    ]
    # Wallet has 0.199 BTC -> 0.099 tracked, 0.100 untracked (pre-180d)
    wallets = [{"currency": "BTC", "wallet_type": "spot", "balance": 0.199, "active_balance": 0.199}]
    res = calculate_portfolio(trades, wallets=wallets, market_stats=_prices())
    btc = next(h for h in res["holdings"] if h["symbol"] == "BTC")
    assert abs(btc["cost_tracked_qty"] - 0.099) < 1e-6
    assert abs(btc["untracked_qty"] - 0.100) < 1e-6
    assert btc["cost_basis_source"] == "partial_history"
    # Unrealized PnL computed ONLY on tracked 0.099 BTC: 0.099 * 60000 - 5000 = +940
    assert abs(btc["unrealized_pnl"] - 940.0) < 0.05


def test_spot_cash_completeness_usdt_dai_irt():
    wallets = [
        {"currency": "USDT", "wallet_type": "spot", "balance": 200.0, "active_balance": 200.0},
        {"currency": "DAI", "wallet_type": "spot", "balance": 100.0, "active_balance": 100.0},
        {"currency": "RLS", "wallet_type": "spot", "balance": 50000000.0, "active_balance": 50000000.0},  # $50 at 1M RLS/USDT
    ]
    res = calculate_portfolio([], wallets=wallets, market_stats=_prices())
    s = res["summary"]
    assert s["usdt_cash"] == 200.0
    assert s["dai_cash"] == 100.0
    assert s["rls_cash_usdt"] == 50.0
    assert s["total_portfolio_value"] == 350.0
    alloc_syms = {item["symbol"] for item in res["charts"]["asset_allocation"]}
    assert {"USDT", "DAI", "IRT"}.issubset(alloc_syms)


def test_adr003_margin_no_double_deduction_and_non_mutation():
    opened_dt = (now_irst() - timedelta(hours=25)).isoformat()  # 3 elapsed 8h periods
    positions = [
        {
            "id": 101,
            "status": "Closed",
            "side": "buy",
            "src_currency": "BTC",
            "dst_currency": "USDT",
            "leverage": 5.0,
            "collateral": 100.0,
            "entry_price": 60000.0,
            "exit_price": 62000.0,
            "pnl": 10.0,  # Already net of open/close/pool share per Nobitex API
            "pool_pnl_share": 1.0,
            "extension_fee": 2.0,
            "opened_at": "2026-08-01T00:00:00+00:00",
            "closed_at": "2026-08-02T00:00:00+00:00",
        },
        {
            "id": 102,
            "status": "Open",
            "side": "buy",
            "src_currency": "USDT",
            "dst_currency": "RLS",
            "leverage": 5.0,
            "collateral": 100000000.0,
            "entry_price": 1000000.0,
            "mark_price": 1020000.0,
            "liquidation_price": 850000.0,
            "pnl": 10000000.0,
            "extension_fee": 500000.0,  # per 8h period -> 3 * 500,000 = 1,500,000
            "opened_at": opened_dt,
        },
        {
            "id": 103,
            "status": "Closed",
            "side": "sell",
            "src_currency": "BTC",
            "dst_currency": "USDT",
            "leverage": 3.0,
            "collateral": 50.0,
            "entry_price": 60000.0,
            "exit_price": 59000.0,
            "pnl": 5.0,
            "extension_fee": 0.0,
            # Mixed naive & aware timestamps (must not raise TypeError)
            "opened_at": "2026-08-01T00:00:00",
            "closed_at": "2026-08-02T00:00:00+00:00",
        },
    ]

    snapshot_before = [dict(p) for p in positions]
    res1 = calculate_positions_summary(positions, _prices())
    res2 = calculate_positions_summary(positions, _prices())

    # Caller input dicts must NOT be mutated
    assert positions == snapshot_before
    assert res1["summary"] == res2["summary"]

    closed_101 = next(p for p in res1["past"] if p["id"] == 101)
    # Closed net_pnl = API pnl (10.0) - extension_fee (2.0) = 8.0 (open/close/pool NOT double-deducted)
    assert closed_101["net_pnl"] == 8.0

    active_102 = res1["active"][0]
    assert active_102["extension_fee_per_period"] == 500000
    assert active_102["extension_fee"] == 1500000


def test_adr013_equity_curve_single_pass_spot_isolation():
    trades = [
        {
            "id": 1,
            "market": "BTC-USDT",
            "type": "buy",
            "price": 50000.0,
            "amount": 0.01,
            "total": 500.0,
            "fee": 0.0,
            "timestamp": "2026-09-01T10:00:00+00:00",
        },
        {
            "id": 2,
            "market": "BTC-USDT",
            "type": "sell",
            "price": 60000.0,
            "amount": 0.01,
            "total": 600.0,
            "fee": 1.0,
            "timestamp": "2026-09-02T10:00:00+00:00",
        },
    ]
    wallets = [{"currency": "USDT", "wallet_type": "spot", "balance": 599.0, "active_balance": 599.0}]
    eq = calculate_equity_curve(
        trades=trades,
        past_positions=[],
        wallets=wallets,
        market_prices=_prices(),
        btc_prices_map={"2026-09-01": 50000.0, "2026-09-02": 60000.0},
        timeframe="all",
        cashflows=[],
    )
    assert eq["metrics"]["current_equity"] == 599.0
    assert eq["metrics"]["net_profit"] == 99.0
    assert eq["metrics"]["winning_days"] == 1
    assert eq["metrics"]["losing_days"] == 0
