from __future__ import annotations

from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from nob import db, server


@pytest.fixture()
def client(isolated_db: Path, stub_client, monkeypatch: pytest.MonkeyPatch):
    # Reset cooldowns & caches between tests
    server._last_positions_on_demand_ts = 0.0
    server._last_btc_on_demand_ts = 0.0
    server._invalidate_open_orders_cache()
    with server._sync_lock:
        server.sync_status["is_syncing"] = False
        server.sync_status["last_error"] = None
        server.sync_status["step"] = None
    # Prevent live WS upstream connection during TestClient lifespan
    monkeypatch.setattr(server.NobitexWSBridge, "start", _noop_async)
    monkeypatch.setattr(server.NobitexWSBridge, "stop", _noop_async)
    with TestClient(server.app, base_url="http://127.0.0.1:8080") as tc:
        yield tc


async def _noop_async(self):
    return None


def test_cswsh_websocket_origin_rejected_and_localhost_allowed(client: TestClient):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws", headers={"origin": "https://evil.example.com"}):
            pass
    assert exc.value.code == 1008

    with pytest.raises(WebSocketDisconnect) as exc_port:
        with client.websocket_connect(
            "/ws",
            headers={"host": "127.0.0.1:8080", "origin": "http://localhost:3000"},
        ):
            pass
    assert exc_port.value.code == 1008

    with client.websocket_connect(
        "/ws",
        headers={"host": "127.0.0.1:8080", "origin": "http://127.0.0.1:8080"},
    ) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "status"
        ws.send_json({"action": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_dns_rebinding_and_cross_origin_mutation_blocked(client: TestClient):
    # Invalid Host header blocked by TrustedHostMiddleware
    bad_host = client.get("/api/stats", headers={"host": "evil.attacker.com"})
    assert bad_host.status_code == 400

    # Cross-origin POST blocked by Origin middleware
    bad_origin = client.post(
        "/api/orders/cancel",
        json={"order_id": 123},
        headers={"origin": "https://evil.example.com"},
    )
    assert bad_origin.status_code == 403


def test_trades_pagination_and_candles_validation(client: TestClient):
    assert client.get("/api/trades?page_size=0").status_code == 422
    assert client.get("/api/trades?page_size=10000").status_code == 422
    assert client.get("/api/trades?page=0").status_code == 422
    ok = client.get("/api/trades?page=1&page_size=25")
    assert ok.status_code == 200
    assert ok.json()["page_size"] == 25

    assert client.get("/api/chart/candles?symbol=BTCUSDT&resolution=999").status_code == 400
    assert client.get("/api/chart/candles?symbol=../../etc&resolution=D").status_code == 400
    assert client.get("/api/chart/candles?symbol=BTCUSDT&resolution=D&from_ts=2000&to_ts=1000").status_code == 400


def test_trade_action_guardrails_and_decimal_formatting(client: TestClient, stub_client):
    # Empty cancel-old rejected unless confirm_all=True
    r_empty = client.post("/api/orders/cancel-old", json={})
    assert r_empty.status_code == 400

    r_confirm = client.post("/api/orders/cancel-old", json={"confirm_all": True})
    assert r_confirm.status_code == 200

    # Invalid position_id / negative amount / missing OCO stop_limit_price rejected
    assert client.post("/api/positions/0/close", json={"execution": "market", "amount": 1}).status_code == 422
    assert client.post("/api/positions/10/close", json={"execution": "market", "amount": -1}).status_code == 422
    assert client.post(
        "/api/positions/10/close",
        json={"execution": "oco", "amount": 0.00001, "price": 70000, "stop_price": 60000},
    ).status_code == 400

    # Small float serialized without scientific notation ('0.00001', not '1e-05')
    r_small = client.post(
        "/api/positions/10/close",
        json={"execution": "market", "amount": 0.00001},
    )
    assert r_small.status_code == 200
    close_calls = [c for c in stub_client.calls if c[0] == "close_position"]
    assert close_calls[-1][1]["amount"] == "0.00001"


def test_on_demand_positions_cooldown_and_sync_lock(client: TestClient, stub_client):
    # Spot-only user (0 positions): second GET within cooldown must NOT hit Nobitex again
    r1 = client.get("/api/positions")
    r2 = client.get("/api/positions")
    assert r1.status_code == 200 and r2.status_code == 200
    pos_fetches = [c for c in stub_client.calls if c[0] == "fetch_all_positions"]
    assert len(pos_fetches) == 2  # active + past once, NOT 4

    # Sync lock prevents overlapping runs
    with server._sync_lock:
        server.sync_status["is_syncing"] = True
    r_sync = client.post("/api/sync")
    assert r_sync.status_code == 200
    assert r_sync.json()["status"] == "in_progress"
    with server._sync_lock:
        server.sync_status["is_syncing"] = False


def test_streaming_csv_export_with_filters(client: TestClient, isolated_db: Path):
    db.upsert_trades(
        [
            {
                "id": 10,
                "orderId": 100,
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
                "id": 11,
                "orderId": 101,
                "market": "ETH-USDT",
                "srcCurrency": "eth",
                "dstCurrency": "usdt",
                "type": "sell",
                "price": 2000,
                "amount": 0.1,
                "total": 200,
                "fee": 0.2,
                "timestamp": "2026-10-02T10:00:00+00:00",
            },
        ],
        db_path=isolated_db,
    )
    resp = client.get("/api/export/csv?symbol=BTC")
    assert resp.status_code == 200
    assert resp.text.startswith("\ufeffid,order_id,")
    assert "BTC-USDT" in resp.text
    assert "ETH-USDT" not in resp.text
