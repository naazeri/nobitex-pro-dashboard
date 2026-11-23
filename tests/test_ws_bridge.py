from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List

from nob import ws_bridge


class FakeLocalWS:
    def __init__(self, delay: float = 0.0, fail: bool = False) -> None:
        self.delay = delay
        self.fail = fail
        self.sent: List[Dict[str, Any]] = []
        self.closed_code: int | None = None

    async def send_json(self, payload: Dict[str, Any]) -> None:
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("broken pipe")
        self.sent.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.closed_code = code


def test_normalize_ws_candle_target_validation():
    assert ws_bridge.normalize_ws_candle_target("btc-usdt", "1h") == ("BTCUSDT", "60")
    assert ws_bridge.normalize_ws_candle_target("1K_SHIB-USDT", "1D") == ("1K_SHIBUSDT", "D")
    assert ws_bridge.normalize_ws_candle_target("BTCUSDT", "1W") is None
    assert ws_bridge.normalize_ws_candle_target("bad symbol!!", "1D") is None
    assert ws_bridge.normalize_ws_candle_target("A" * 40, "1D") is None


def test_subscription_cap_and_slow_client_isolation(isolated_db: Path, monkeypatch):
    async def _run():
        bridge = ws_bridge.NobitexWSBridge(client_factory=lambda: None, db_path=isolated_db)
        fast = FakeLocalWS()
        slow = FakeLocalWS(delay=0.3)
        monkeypatch.setattr(ws_bridge, "LOCAL_WS_SEND_TIMEOUT_SEC", 0.05)

        await bridge.register_client(fast)
        await bridge.register_client(slow)

        # Subscribe 25 distinct symbols -> capped at MAX_CANDLE_SUBS_PER_CLIENT (20)
        for i in range(25):
            await bridge.handle_client_message(
                fast,
                {"action": "subscribe_candle", "symbol": f"TOK{i}USDT", "resolution": "15m"},
            )
        assert len(bridge._client_candles[fast]) == ws_bridge.MAX_CANDLE_SUBS_PER_CLIENT

        # Broadcast: fast gets message, slow times out and is unregistered
        await bridge._broadcast({"type": "market_stats", "data": {"btc-usdt": {"latest": 60000}}})
        assert any(m.get("type") == "market_stats" for m in fast.sent)
        assert slow not in bridge._clients
        assert fast in bridge._clients

        # Graceful stop closes remaining clients with 1001
        await bridge.stop()
        assert fast.closed_code == 1001
        assert len(bridge._clients) == 0

    asyncio.run(_run())


def test_dirty_price_flush_preserves_batch_on_db_error(isolated_db: Path, monkeypatch):
    async def _run():
        bridge = ws_bridge.NobitexWSBridge(client_factory=lambda: None, db_path=isolated_db)
        bridge._dirty_prices["btc-usdt"] = {"latest": 65000.0}

        def _boom(*args, **kwargs):
            raise RuntimeError("sqlite busy")

        monkeypatch.setattr(ws_bridge, "upsert_market_prices", _boom)
        await bridge._flush_dirty_prices(force=True)
        # Entry must still be preserved for retry
        assert "btc-usdt" in bridge._dirty_prices
        assert bridge._dirty_prices["btc-usdt"]["latest"] == 65000.0

    asyncio.run(_run())
