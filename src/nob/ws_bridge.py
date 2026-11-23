from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from nob.db import _safe_float, upsert_market_prices, upsert_orders, upsert_trades
from nob.nobitex_client import NobitexAPIError, NobitexAuthError, NobitexClient
from nob.time_utils import now_irst

logger = logging.getLogger(__name__)

try:
    import websockets
except ImportError:
    websockets = None  # Graceful offline / missing-dependency fallback

NOBITEX_WS_URL = "wss://ws.nobitex.ir/connection/websocket"
JWT_REFRESH_INTERVAL_SEC = 1000.0  # Official JWT TTL is 1200s
DB_PRICE_FLUSH_INTERVAL_SEC = 15.0
MAX_CANDLE_SUBS_PER_CLIENT = 20
LOCAL_WS_SEND_TIMEOUT_SEC = 2.0
_WS_SYMBOL_RE = re.compile(r"^[A-Z0-9_]{2,24}$")

VALID_WS_CANDLE_RESOLUTIONS = {
    "1",
    "5",
    "15",
    "30",
    "60",
    "180",
    "240",
    "360",
    "720",
    "D",
    "1D",
    "2D",
    "3D",
}


def normalize_ws_candle_target(symbol: str, resolution: str) -> Optional[Tuple[str, str]]:
    """
    Normalize (symbol, resolution) into Nobitex Centrifugo candle channel parameters.
    Returns (SYMBOL, RESOLUTION) or None if the resolution is unsupported on WS (e.g. '1W').
    """
    clean_sym = str(symbol or "").replace("-", "").replace("/", "").strip().upper()
    if not clean_sym or not _WS_SYMBOL_RE.match(clean_sym):
        return None

    res_raw = str(resolution or "").strip().upper()
    res_map = {
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
        "D": "D",
    }
    res_norm = res_map.get(res_raw, res_raw)
    if res_norm not in VALID_WS_CANDLE_RESOLUTIONS:
        return None
    return clean_sym, res_norm


def parse_centrifugo_frame(raw_text: Any) -> List[Dict[str, Any]]:
    """
    Parse a Centrifugo JSON text frame which may contain multiple newline-separated JSON objects.
    """
    if isinstance(raw_text, bytes):
        raw_text = raw_text.decode("utf-8", errors="ignore")
    if not isinstance(raw_text, str):
        return []

    messages: List[Dict[str, Any]] = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                messages.append(obj)
        except Exception:
            continue
    return messages


def _ms_to_utc_iso(ms_val: Any) -> str:
    """Convert epoch milliseconds (or seconds if < 1e11) to UTC ISO-8601 string."""
    try:
        num = float(ms_val)
        if num > 1e11:
            num = num / 1000.0
        return datetime.fromtimestamp(num, tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return datetime.now(tz=timezone.utc).isoformat()


def normalize_ws_order_event(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Normalize a Nobitex `private:orders#{param}` payload into the schema expected by db.upsert_orders().
    """
    order_id = raw.get("orderId") or raw.get("id")
    if not order_id:
        return None
    try:
        order_id_int = int(order_id)
    except (ValueError, TypeError):
        return None

    src = str(raw.get("srcCurrency") or "").strip().upper()
    dst = str(raw.get("dstCurrency") or "").strip().upper()
    market = f"{src}-{dst}" if src and dst else str(raw.get("market") or "").upper()

    side = str(raw.get("side") or raw.get("type") or "").strip().lower()
    market_type = str(raw.get("marketType") or raw.get("tradeType") or "Spot").strip()
    if market_type.lower() == "margin":
        market_type = "Margin"
    else:
        market_type = "Spot"

    execution = str(raw.get("orderType") or raw.get("execution") or "Limit").strip()
    price = _safe_float(raw.get("price"))
    amount = _safe_float(raw.get("amount"))
    filled = _safe_float(raw.get("filledAmount", raw.get("matchedAmount")))
    unmatched = max(0.0, amount - filled)
    total_price = _safe_float(raw.get("totalOrderPrice", raw.get("totalPrice")))
    if total_price == 0.0 and price > 0 and amount > 0:
        total_price = price * amount

    status = str(raw.get("status") or "Active").strip()
    event_time = raw.get("eventTime") or raw.get("created_at")
    created_at = (
        _ms_to_utc_iso(event_time)
        if isinstance(event_time, (int, float)) or (isinstance(event_time, str) and event_time.isdigit())
        else str(event_time or datetime.now(tz=timezone.utc).isoformat())
    )

    return {
        "id": order_id_int,
        "tradeType": market_type,
        "market": market,
        "type": side,
        "execution": execution,
        "price": price,
        "amount": amount,
        "totalPrice": total_price,
        "matchedAmount": filled,
        "unmatchedAmount": unmatched,
        "leverage": _safe_float(raw.get("leverage"), 1.0),
        "status": status,
        "created_at": created_at,
    }


def normalize_ws_trade_event(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Normalize a Nobitex `private:trades#{param}` payload into the schema expected by db.upsert_trades().
    Strictly uses `time` (epoch ms) instead of deprecated `timestamp`.
    """
    trade_id = raw.get("id")
    if not trade_id:
        return None
    try:
        trade_id_int = int(trade_id)
    except (ValueError, TypeError):
        return None

    order_id = raw.get("orderId")
    try:
        order_id_int = int(order_id) if order_id is not None else None
    except (ValueError, TypeError):
        order_id_int = None

    src = str(raw.get("srcCurrency") or "").strip().lower()
    dst = str(raw.get("dstCurrency") or "").strip().lower()
    market = f"{src.upper()}-{dst.upper()}" if src and dst else str(raw.get("market") or "").upper()
    side = str(raw.get("type") or raw.get("side") or "").strip().lower()

    # Strictly prefer `time` (ms) over deprecated `timestamp`
    raw_time = raw.get("time")
    if raw_time is not None:
        ts_iso = _ms_to_utc_iso(raw_time)
    else:
        ts_iso = str(raw.get("timestamp") or datetime.now(tz=timezone.utc).isoformat())

    price = _safe_float(raw.get("price"))
    amount = _safe_float(raw.get("amount"))
    total = _safe_float(raw.get("total"))
    if total == 0.0 and price > 0 and amount > 0:
        total = price * amount
    fee = _safe_float(raw.get("fee"))

    return {
        "id": trade_id_int,
        "orderId": order_id_int,
        "market": market,
        "srcCurrency": src,
        "dstCurrency": dst,
        "type": side,
        "price": price,
        "amount": amount,
        "total": total,
        "fee": fee,
        "timestamp": ts_iso,
        "isMaker": bool(raw.get("isMaker", False)),
    }


class NobitexWSBridge:
    """
    Backend Centrifugo WebSocket client -> Local FastAPI WebSocket broadcaster.

    - Connects to wss://ws.nobitex.ir/connection/websocket using plain Centrifugo JSON protocol
      (no delta:'fossil', replies `{}` to `{}` pings within 20s).
    - Subscribes to `public:market-stats-all`, on-demand ref-counted `public:candle-{SYM}-{RES}`,
      and (if API keys exist) `private:orders#{param}` and `private:trades#{param}` with JWT refresh.
    - Degrades gracefully when offline or when `websockets` is unavailable (never crashes FastAPI).
    """

    def __init__(
        self,
        client_factory: Callable[[], NobitexClient],
        db_path: Optional[Path] = None,
        ws_url: str = NOBITEX_WS_URL,
        on_order_update: Optional[Callable[[], None]] = None,
        price_flush_interval: float = DB_PRICE_FLUSH_INTERVAL_SEC,
        jwt_refresh_interval: float = JWT_REFRESH_INTERVAL_SEC,
    ):
        self._client_factory = client_factory
        self._db_path = db_path
        self.ws_url = ws_url
        self._on_order_update = on_order_update
        self._price_flush_interval = float(price_flush_interval)
        self._jwt_refresh_interval = float(jwt_refresh_interval)

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._flush_task: Optional[asyncio.Task] = None
        self._upstream_ws: Any = None
        self._upstream_connected = False
        self._authenticated = False
        self._ws_auth_param: Optional[str] = None
        self._cmd_id = 0
        self._last_message_at: Optional[str] = None
        self._last_error: Optional[str] = None

        # Throttled SQLite flush buffer for live prices
        self._dirty_prices: Dict[str, Dict[str, Any]] = {}
        self._last_db_flush = 0.0

        # Connected local FastAPI WebSocket tabs & ref-counted candle subscriptions
        self._clients: Set[Any] = set()
        self._client_candles: Dict[Any, Set[Tuple[str, str]]] = {}
        self._candle_refcounts: Dict[Tuple[str, str], int] = {}

    def _next_id(self) -> int:
        self._cmd_id += 1
        return self._cmd_id

    def get_status(self) -> Dict[str, Any]:
        return {
            "enabled": websockets is not None,
            "running": self._running,
            "upstream_connected": self._upstream_connected,
            "authenticated": self._authenticated,
            "clients_count": len(self._clients),
            "subscribed_candles": sorted(
                f"{sym}:{res}" for sym, res in self._candle_refcounts.keys()
            ),
            "last_message_at": self._last_message_at,
            "last_error": self._last_error,
        }

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        if websockets is None:
            self._last_error = "websockets package not installed"
            logger.info("websockets package not installed; real-time WS bridge disabled (REST fallback active).")
            return
        self._task = asyncio.create_task(self._run_loop())
        self._flush_task = asyncio.create_task(self._price_flush_loop())

    async def stop(self) -> None:
        self._running = False
        if self._upstream_ws is not None:
            try:
                await self._upstream_ws.close()
            except Exception:
                pass
            self._upstream_ws = None
        for task_attr in ("_task", "_flush_task"):
            task = getattr(self, task_attr, None)
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
                setattr(self, task_attr, None)
        # Gracefully close all connected local browser WebSocket clients
        for ws in list(self._clients):
            try:
                await asyncio.wait_for(ws.close(code=1001), timeout=1.0)
            except Exception:
                pass
            self._clients.discard(ws)
            self._client_candles.pop(ws, None)
        self._candle_refcounts.clear()
        await self._flush_dirty_prices(force=True)
        self._upstream_connected = False
        self._authenticated = False

    async def _price_flush_loop(self) -> None:
        """Periodically persist dirty market prices to SQLite without blocking upstream WS reads."""
        while self._running:
            await asyncio.sleep(self._price_flush_interval)
            if not self._running:
                break
            await self._flush_dirty_prices(force=True)

    async def _send_upstream(self, payload: Dict[str, Any], send_fn: Optional[Callable[[str], Awaitable[None]]] = None) -> None:
        raw = json.dumps(payload, separators=(",", ":"))
        if send_fn is not None:
            await send_fn(raw)
        elif self._upstream_ws is not None:
            await self._upstream_ws.send(raw)

    async def register_client(self, ws: Any) -> None:
        self._clients.add(ws)
        self._client_candles[ws] = set()
        try:
            await ws.send_json({
                "type": "status",
                "upstream_connected": self._upstream_connected,
                "authenticated": self._authenticated,
            })
        except Exception:
            await self.unregister_client(ws)

    async def unregister_client(self, ws: Any) -> None:
        self._clients.discard(ws)
        subs = self._client_candles.pop(ws, set())
        for key in list(subs):
            await self._decrement_candle_ref(key)

    async def handle_client_message(self, ws: Any, msg: Dict[str, Any]) -> None:
        if not isinstance(msg, dict):
            return
        action = str(msg.get("action") or "").strip().lower()
        if action == "ping":
            try:
                await ws.send_json({"type": "pong"})
            except Exception:
                pass
            return

        if action == "subscribe_candle":
            target = normalize_ws_candle_target(msg.get("symbol", ""), msg.get("resolution", ""))
            if not target:
                return
            client_subs = self._client_candles.setdefault(ws, set())
            if target not in client_subs:
                if len(client_subs) >= MAX_CANDLE_SUBS_PER_CLIENT:
                    return
                client_subs.add(target)
                await self._increment_candle_ref(target)
            return

        if action == "unsubscribe_candle":
            target = normalize_ws_candle_target(msg.get("symbol", ""), msg.get("resolution", ""))
            if not target:
                return
            client_subs = self._client_candles.get(ws)
            if client_subs and target in client_subs:
                client_subs.discard(target)
                await self._decrement_candle_ref(target)

    async def _increment_candle_ref(self, target: Tuple[str, str]) -> None:
        prev = self._candle_refcounts.get(target, 0)
        self._candle_refcounts[target] = prev + 1
        if prev == 0 and self._upstream_connected and self._upstream_ws is not None:
            sym, res = target
            channel = f"public:candle-{sym}-{res}"
            try:
                await self._send_upstream({"id": self._next_id(), "subscribe": {"channel": channel}})
            except Exception:
                pass

    async def _decrement_candle_ref(self, target: Tuple[str, str]) -> None:
        prev = self._candle_refcounts.get(target, 0)
        if prev <= 1:
            self._candle_refcounts.pop(target, None)
            if prev == 1 and self._upstream_connected and self._upstream_ws is not None:
                sym, res = target
                channel = f"public:candle-{sym}-{res}"
                try:
                    await self._send_upstream({"id": self._next_id(), "unsubscribe": {"channel": channel}})
                except Exception:
                    pass
        else:
            self._candle_refcounts[target] = prev - 1

    async def _broadcast(self, payload: Dict[str, Any], target_candle: Optional[Tuple[str, str]] = None) -> None:
        if not self._clients:
            return
        targets: List[Any] = []
        for ws in list(self._clients):
            if target_candle is not None:
                if target_candle not in self._client_candles.get(ws, set()):
                    continue
            targets.append(ws)
        if not targets:
            return

        async def _send_one(client_ws: Any) -> Optional[Any]:
            try:
                await asyncio.wait_for(
                    client_ws.send_json(payload),
                    timeout=LOCAL_WS_SEND_TIMEOUT_SEC,
                )
                return None
            except Exception:
                return client_ws

        results = await asyncio.gather(*(_send_one(ws) for ws in targets), return_exceptions=False)
        for dead_ws in results:
            if dead_ws is not None:
                await self.unregister_client(dead_ws)

    async def _broadcast_status(self) -> None:
        await self._broadcast({
            "type": "status",
            "upstream_connected": self._upstream_connected,
            "authenticated": self._authenticated,
        })

    async def _fetch_auth_credentials(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Fetch (jwt_token, websocket_auth_param) using NobitexClient in a worker thread.
        Returns (None, None) if credentials are absent or auth fails (public-only fallback).
        """
        client = self._client_factory()
        if not client.api_key or not client.secret:
            return None, None
        try:
            token = await asyncio.to_thread(client.fetch_ws_token)
            if not self._ws_auth_param:
                profile = await asyncio.to_thread(client.fetch_user_profile)
                param = profile.get("websocketAuthParam")
                if isinstance(param, str) and param.strip():
                    self._ws_auth_param = param.strip()
            return token, self._ws_auth_param
        except (NobitexAuthError, NobitexAPIError, Exception) as exc:
            self._last_error = f"WS auth skipped (public-only): {exc}"
            return None, None

    async def _jwt_refresh_loop(self, ws: Any) -> None:
        """Refresh Centrifugo JWT every ~1000s (TTL is 1200s) while connected."""
        while self._running and self._upstream_connected and self._upstream_ws is ws:
            await asyncio.sleep(self._jwt_refresh_interval)
            if not (self._running and self._upstream_connected and self._upstream_ws is ws):
                break
            try:
                client = self._client_factory()
                new_token = await asyncio.to_thread(client.fetch_ws_token)
                await self._send_upstream({"refresh": {"token": new_token}, "id": self._next_id()})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = f"WS JWT refresh failed: {exc}"
                break

    async def _flush_dirty_prices(self, force: bool = False) -> None:
        if not self._dirty_prices:
            return
        now = time.monotonic()
        if not force and (now - self._last_db_flush) < self._price_flush_interval:
            return
        batch = dict(self._dirty_prices)
        self._dirty_prices.clear()
        try:
            await asyncio.to_thread(upsert_market_prices, batch, self._db_path)
            self._last_db_flush = now
        except Exception as exc:
            # Preserve unsaved entries that weren't superseded by newer ticks
            for k, v in batch.items():
                self._dirty_prices.setdefault(k, v)
            self._last_error = f"SQLite price flush error: {exc}"
            logger.warning("SQLite price flush error: %s", exc)

    async def _process_raw_frame(
        self,
        raw_text: Any,
        send_fn: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> None:
        """
        Process a single incoming Centrifugo frame (potentially containing multiple newline-delimited JSON objects).
        """
        frames = parse_centrifugo_frame(raw_text)
        if not frames:
            return

        self._last_message_at = now_irst().isoformat()

        for msg in frames:
            # 1. Server heartbeat ping: empty JSON `{}` -> MUST reply `{}` within 20s
            if msg == {}:
                await self._send_upstream({}, send_fn=send_fn)
                continue

            push = msg.get("push")
            if not isinstance(push, dict):
                continue

            channel = str(push.get("channel") or "")
            pub = push.get("pub")
            if not isinstance(pub, dict):
                continue
            data = pub.get("data")
            if data is None:
                continue

            if channel == "public:market-stats-all" and isinstance(data, dict):
                await self._handle_market_stats(data)
            elif channel.startswith("public:candle-") and isinstance(data, dict):
                await self._handle_candle_update(channel, data)
            elif channel.startswith("private:orders#") and isinstance(data, dict):
                await self._handle_private_order(data)
            elif channel.startswith("private:trades#") and isinstance(data, dict):
                await self._handle_private_trade(data)

    async def _handle_market_stats(self, data: Dict[str, Any]) -> None:
        normalized: Dict[str, Dict[str, Any]] = {}
        for market_key, info in data.items():
            if not isinstance(info, dict):
                continue
            m_lower = str(market_key).strip().lower()
            latest = _safe_float(info.get("latest"))
            if latest <= 0:
                continue
            entry = {
                "latest": latest,
                "mark": _safe_float(info.get("mark"), latest),
                "dayChange": _safe_float(info.get("dayChange")),
                "dayHigh": _safe_float(info.get("dayHigh")),
                "dayLow": _safe_float(info.get("dayLow")),
                "isClosed": bool(info.get("isClosed", False)),
            }
            normalized[m_lower] = entry
            self._dirty_prices[m_lower] = entry

        if not normalized:
            return

        await self._broadcast({
            "type": "market_stats",
            "data": normalized,
            "ts": now_irst().isoformat(),
        })

    async def _handle_candle_update(self, channel: str, data: Dict[str, Any]) -> None:
        # Channel format: public:candle-{SYMBOL}-{RESOLUTION}
        suffix = channel[len("public:candle-"):]
        parts = suffix.rsplit("-", 1)
        if len(parts) != 2:
            return
        sym, res = parts[0].upper(), parts[1].upper()
        if res == "1D":
            res = "D"

        t_val = data.get("t")
        if t_val is None:
            return
        try:
            t_sec = int(t_val)
        except (ValueError, TypeError):
            return

        bar = {
            "time": t_sec * 1000,
            "open": _safe_float(data.get("o")),
            "high": _safe_float(data.get("h")),
            "low": _safe_float(data.get("l")),
            "close": _safe_float(data.get("c")),
            "volume": _safe_float(data.get("v")),
        }
        await self._broadcast(
            {
                "type": "candle",
                "symbol": sym,
                "resolution": res,
                "bar": bar,
            },
            target_candle=(sym, res),
        )

    async def _handle_private_order(self, data: Dict[str, Any]) -> None:
        normalized = normalize_ws_order_event(data)
        if not normalized:
            return
        try:
            await asyncio.to_thread(upsert_orders, [normalized], self._db_path)
            if self._on_order_update:
                self._on_order_update()
        except Exception as exc:
            self._last_error = f"WS order upsert error: {exc}"
        await self._broadcast({"type": "private_order", "order": normalized})

    async def _handle_private_trade(self, data: Dict[str, Any]) -> None:
        normalized = normalize_ws_trade_event(data)
        if not normalized:
            return
        try:
            await asyncio.to_thread(upsert_trades, [normalized], None, self._db_path)
        except Exception as exc:
            self._last_error = f"WS trade upsert error: {exc}"
        await self._broadcast({"type": "private_trade", "trade": normalized})

    async def _run_loop(self) -> None:
        backoff = 2.0
        while self._running:
            refresh_task: Optional[asyncio.Task] = None
            try:
                token, auth_param = await self._fetch_auth_credentials()

                async with websockets.connect(
                    self.ws_url,
                    ping_interval=None,
                    ping_timeout=None,
                    close_timeout=5,
                    open_timeout=15,
                    max_size=4 * 1024 * 1024,
                ) as ws:
                    self._upstream_ws = ws
                    self._last_error = None
                    backoff = 2.0

                    # Centrifugo connect handshake (plain JSON, NO delta:'fossil')
                    if token:
                        await self._send_upstream({"connect": {"token": token}, "id": self._next_id()})
                        self._authenticated = bool(auth_param)
                    else:
                        await self._send_upstream({"connect": {}, "id": self._next_id()})
                        self._authenticated = False

                    self._upstream_connected = True
                    await self._broadcast_status()

                    # Subscribe to all-market stats
                    await self._send_upstream({
                        "id": self._next_id(),
                        "subscribe": {"channel": "public:market-stats-all"},
                    })

                    # Subscribe to private order & trade channels if authenticated
                    if token and auth_param:
                        await self._send_upstream({
                            "id": self._next_id(),
                            "subscribe": {"channel": f"private:orders#{auth_param}"},
                        })
                        await self._send_upstream({
                            "id": self._next_id(),
                            "subscribe": {"channel": f"private:trades#{auth_param}"},
                        })
                        refresh_task = asyncio.create_task(self._jwt_refresh_loop(ws))

                    # Re-subscribe any active client candle channels
                    for sym, res in list(self._candle_refcounts.keys()):
                        await self._send_upstream({
                            "id": self._next_id(),
                            "subscribe": {"channel": f"public:candle-{sym}-{res}"},
                        })

                    async for raw_frame in ws:
                        if not self._running:
                            break
                        await self._process_raw_frame(raw_frame)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._last_error = str(exc)
            finally:
                if refresh_task is not None:
                    refresh_task.cancel()
                    try:
                        await refresh_task
                    except Exception:
                        pass
                was_connected = self._upstream_connected
                self._upstream_ws = None
                self._upstream_connected = False
                self._authenticated = False
                if was_connected:
                    await self._broadcast_status()

            if not self._running:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, 30.0)
