from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
import pytest

from nob import db, server


class StubNobitexClient:
    """Offline mock NobitexClient for deterministic FastAPI and WS tests."""

    def __init__(self) -> None:
        self.api_key = "test-key"
        self.secret = ""
        self.calls: List[tuple[str, Dict[str, Any]]] = []
        self.positions_active: List[Dict[str, Any]] = []
        self.positions_past: List[Dict[str, Any]] = []
        self.open_orders: List[Dict[str, Any]] = []
        self.market_stats: Dict[str, Any] = {
            "btc-usdt": {"latest": "65000", "dayChange": "1.2", "dayHigh": "66000", "dayLow": "64000"},
            "usdt-rls": {"latest": "1000000", "dayChange": "0.1", "dayHigh": "1010000", "dayLow": "990000"},
        }
        self.cancel_old_response: Dict[str, Any] = {"status": "ok"}
        self.close_position_response: Dict[str, Any] = {"status": "ok"}
        self.edit_collateral_response: Dict[str, Any] = {"status": "ok"}
        self.udf_response: Dict[str, Any] = {"s": "ok", "t": [1700000000], "o": [65000], "h": [65500], "l": [64500], "c": [65200], "v": [10]}

    def close(self) -> None:
        pass

    def fetch_all_positions(self, status: str = "past", **kwargs: Any) -> List[Dict[str, Any]]:
        self.calls.append(("fetch_all_positions", {"status": status, **kwargs}))
        return list(self.positions_active if status == "active" else self.positions_past)

    def fetch_all_orders(self, status: str = "open", **kwargs: Any) -> List[Dict[str, Any]]:
        self.calls.append(("fetch_all_orders", {"status": status, **kwargs}))
        return list(self.open_orders)

    def fetch_market_stats(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(("fetch_market_stats", kwargs))
        return dict(self.market_stats)

    def fetch_udf_history(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(("fetch_udf_history", kwargs))
        return dict(self.udf_response)

    def cancel_order(self, order_id: int) -> Dict[str, Any]:
        self.calls.append(("cancel_order", {"order_id": order_id}))
        return {"status": "ok", "order": order_id}

    def cancel_old_orders(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(("cancel_old_orders", kwargs))
        return dict(self.cancel_old_response)

    def close_position(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(("close_position", kwargs))
        return dict(self.close_position_response)

    def edit_position_collateral(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(("edit_position_collateral", kwargs))
        return dict(self.edit_collateral_response)

    def fetch_position_status(self, position_id: int) -> Dict[str, Any]:
        self.calls.append(("fetch_position_status", {"position_id": position_id}))
        return {}


@pytest.fixture()
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect DEFAULT_DB_PATH to an isolated temporary SQLite database."""
    test_db = tmp_path / "test_portfolio.db"
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", test_db)
    db._initialized_dbs.clear()
    db.init_db(test_db, force=True)
    return test_db


@pytest.fixture()
def stub_client(monkeypatch: pytest.MonkeyPatch) -> StubNobitexClient:
    client = StubNobitexClient()
    monkeypatch.setattr(server, "get_nobitex_client", lambda: client)
    return client
