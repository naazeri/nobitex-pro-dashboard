import os
import re
import time
import base64
import json
import threading
from collections import deque
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable, Tuple
from urllib.parse import urlencode

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(dotenv_path=None):
        if not dotenv_path:
            return
        p = Path(dotenv_path)
        if not p.is_file():
            return
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip("'\"")
                if k and k not in os.environ:
                    os.environ[k] = v

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

DEFAULT_BASE_URL = "https://apiv2.nobitex.ir"
USER_AGENT = "TraderBot/NobAPI-2.1.0"

# Per-endpoint rate limits: (max_calls_in_window, window_seconds, min_spacing_seconds)
# Includes a safety margin below official Nobitex limits to prevent 429 & 2-min token bans.
ENDPOINT_RATE_LIMITS: Dict[str, Tuple[int, float, float]] = {
    "/market/trades/list": (28, 60.0, 2.05),          # Official: 30 / min
    "/market/orders/list": (28, 60.0, 2.05),          # Official: 30 / min
    "/market/orders/update-status": (85, 60.0, 0.2),  # Official: 90 / min
    "/market/orders/cancel-old": (28, 60.0, 2.05),    # Official: 30 / min
    "/positions/list": (28, 600.0, 0.5),              # Official: 30 / 10 min
    "/positions/{id}/status": (95, 600.0, 0.2),       # Official: 100 / 10 min
    "/market/stats": (18, 60.0, 3.1),                 # Official: 20 / min
    "/users/wallets/list": (18, 60.0, 0.5),           # Official: 20 / min
    "/v2/wallets": (14, 60.0, 0.5),                   # Official: 15 / min
    "/market/udf/history": (55, 60.0, 1.05),          # Official: 60 / min
    "/margin/markets/list": (28, 60.0, 2.05),         # Official: 30 / min
    "/v2/options": (28, 60.0, 1.0),                   # Public system options
    "/users/profile": (28, 60.0, 1.0),                # User profile & websocketAuthParam
    "/auth/ws/token/": (25, 60.0, 1.0),               # WebSocket JWT token (1200s TTL)
    "/users/portfolio/last-week-daily-profit": (9, 180.0, 2.0),        # Official: 10 / 3 min
    "/users/portfolio/last-week-daily-total-profit": (9, 180.0, 2.0),  # Official: 10 / 3 min
    "/users/wallets/deposits/list": (18, 60.0, 1.0),                   # User deposits list
    "/users/wallets/withdraws/list": (18, 60.0, 1.0),                  # User withdrawals list
}

_NUMERIC_SEGMENT_RE = re.compile(r"/\d+(?=/|$)")


class NobitexAPIError(RuntimeError):
    """Structured error returned by Nobitex REST API."""

    def __init__(
        self,
        status_code: int,
        code: Optional[str],
        message: str,
        payload: Optional[Dict[str, Any]] = None,
        backoff: Optional[float] = None,
    ):
        self.status_code = status_code
        self.code = code or "UnknownError"
        self.message = message or self.code
        self.payload = payload
        self.backoff = backoff
        super().__init__(f"Nobitex API Error (HTTP {status_code}, code={self.code}): {self.message}")


class NobitexAuthError(NobitexAPIError):
    """Authentication or permission error (401/403 or missing credentials). Never retried."""


class NobitexRateLimitError(NobitexAPIError):
    """Rate limit exceeded (HTTP 429) after exhausting retries."""


def _normalize_endpoint_key(path: str) -> str:
    """Normalize endpoint path for rate-limit bucketing (strips query & numeric IDs)."""
    base = path.split("?", 1)[0]
    return _NUMERIC_SEGMENT_RE.sub("/{id}", base)


class _EndpointRateLimiter:
    """Thread-safe sliding-window + minimum-spacing rate limiter per endpoint."""

    def __init__(
        self,
        rules: Optional[Dict[str, Tuple[int, float, float]]] = None,
        clock_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self._rules = rules if rules is not None else ENDPOINT_RATE_LIMITS
        self._clock = clock_fn
        self._sleep = sleep_fn
        self._lock = threading.Lock()
        self._history: Dict[str, deque] = {}

    def acquire(self, path: str) -> None:
        key = _normalize_endpoint_key(path)
        rule = self._rules.get(key)
        if not rule:
            return
        max_calls, window_sec, min_spacing = rule

        while True:
            wait_sec = 0.0
            with self._lock:
                now = self._clock()
                dq = self._history.setdefault(key, deque())
                cutoff = now - window_sec + 1e-6
                while dq and dq[0] <= cutoff:
                    dq.popleft()

                if dq and min_spacing > 0:
                    since_last = now - dq[-1]
                    if since_last + 1e-6 < min_spacing:
                        wait_sec = max(wait_sec, min_spacing - since_last)

                if len(dq) >= max_calls:
                    window_wait = (dq[0] + window_sec) - now
                    if window_wait > 1e-6:
                        wait_sec = max(wait_sec, window_wait)

                if wait_sec <= 1e-6:
                    dq.append(now)
                    return

            self._sleep(wait_sec)


def find_env_file() -> Optional[Path]:
    """Locate .env file in project or standard locations."""
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent.parent / ".env",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def load_credentials(env_path: Optional[Path] = None) -> tuple[str, str]:
    """Load API and SECRET from .env file."""
    if env_path is None:
        env_path = find_env_file()

    if env_path and env_path.is_file():
        load_dotenv(dotenv_path=env_path)

    api_key = os.getenv("API", "").strip()
    secret = os.getenv("SECRET", "").strip()

    if not api_key or not secret:
        raise ValueError(
            "API or SECRET not found in environment or .env file. "
            "Please check that API and SECRET are set."
        )

    return api_key, secret


def sign_request(secret_b64: str, method: str, full_path: str, raw_body: str = "") -> tuple[str, str]:
    """
    Generate Ed25519 signature per Nobitex specifications:
    payload = timestamp + METHOD + full_path + raw_body
    Returns (signature_urlsafe_b64, timestamp).
    """
    timestamp = str(int(time.time()))
    payload = f"{timestamp}{method.upper()}{full_path}{raw_body}".encode("utf-8")

    # Ensure padding for base64 decode
    secret_clean = secret_b64.strip()
    padding = len(secret_clean) % 4
    if padding:
        secret_clean += "=" * (4 - padding)

    private_key_bytes = base64.urlsafe_b64decode(secret_clean)
    private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    raw_signature = private_key.sign(payload)
    signature_b64 = base64.urlsafe_b64encode(raw_signature).decode("utf-8")

    return signature_b64, timestamp


class NobitexClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        secret: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        require_auth: bool = False,
        sleep_fn: Callable[[float], None] = time.sleep,
        clock_fn: Callable[[], float] = time.monotonic,
    ):
        if not api_key or not secret:
            try:
                loaded_key, loaded_secret = load_credentials()
                api_key = api_key or loaded_key
                secret = secret or loaded_secret
            except ValueError:
                if require_auth:
                    raise
                api_key = api_key or ""
                secret = secret or ""

        self.api_key = api_key
        self.secret = secret
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self._sleep = sleep_fn
        self._rate_limiter = _EndpointRateLimiter(clock_fn=clock_fn, sleep_fn=sleep_fn)

    def close(self) -> None:
        """Close underlying HTTP session."""
        try:
            self.session.close()
        except Exception:
            pass

    def __enter__(self) -> "NobitexClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _parse_json_safe(self, response: requests.Response) -> Dict[str, Any]:
        try:
            data = response.json()
            return data if isinstance(data, dict) else {"data": data}
        except Exception:
            return {}

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        body_dict: Optional[Dict[str, Any]] = None,
        auth: bool = True,
        timeout: float = 20.0,
        max_retries: int = 3,
        check_status_ok: bool = True,
    ) -> Dict[str, Any]:
        """
        Centralized HTTP transport with:
        - Per-endpoint rate limiting
        - Fresh Ed25519 signing on every attempt
        - HTTP 429 backOff sleep & retry
        - Immediate fail-fast on HTTP 401/403 (0 retries to prevent 30-min IP ban)
        - Exponential backoff on 5xx / transient network errors
        """
        method_upper = method.upper()
        if auth and (not self.api_key or not self.secret):
            raise NobitexAuthError(
                401,
                "MissingCredentials",
                "API or SECRET not configured in environment or .env file.",
            )

        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        full_path = f"{path}?{urlencode(clean_params)}" if clean_params else path
        raw_body = json.dumps(body_dict, separators=(",", ":")) if body_dict is not None else ""
        url = f"{self.base_url}{full_path}"

        last_error: Optional[Exception] = None

        for attempt in range(max_retries + 1):
            self._rate_limiter.acquire(path)

            headers: Dict[str, str] = {
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            }
            if body_dict is not None:
                headers["Content-Type"] = "application/json"

            if auth:
                sig, ts = sign_request(self.secret, method_upper, full_path, raw_body)
                headers["Nobitex-Key"] = self.api_key
                headers["Nobitex-Signature"] = sig
                headers["Nobitex-Timestamp"] = ts

            try:
                response = self.session.request(
                    method=method_upper,
                    url=url,
                    headers=headers,
                    data=raw_body.encode("utf-8") if raw_body else None,
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                last_error = NobitexAPIError(0, "NetworkError", str(exc))
                if attempt < max_retries:
                    self._sleep(float(2 ** attempt))
                    continue
                raise last_error from exc

            status = response.status_code
            data = self._parse_json_safe(response)

            # Fail fast on auth/permission errors — NEVER retry (prevents 30-min IP ban)
            if status in (401, 403):
                code = data.get("code") or ("Unauthorized" if status == 401 else "Forbidden")
                msg = data.get("message") or response.text or f"HTTP {status}"
                raise NobitexAuthError(status, code, msg, payload=data)

            # Handle 429 Too Many Requests using server-provided backOff
            if status == 429:
                code = data.get("code") or "TooManyRequests"
                msg = data.get("message") or response.text or "Too Many Requests"
                try:
                    raw_backoff = float(data.get("backOff", 5.0))
                except (ValueError, TypeError):
                    raw_backoff = 5.0
                backoff = min(max(raw_backoff, 1.0), 60.0)
                if attempt < max_retries:
                    self._sleep(backoff + 0.5)
                    continue
                raise NobitexRateLimitError(status, code, msg, payload=data, backoff=backoff)

            # Retry transient 5xx server errors
            if status >= 500:
                code = data.get("code") or f"HTTP{status}"
                msg = data.get("message") or response.text or f"HTTP {status}"
                last_error = NobitexAPIError(status, code, msg, payload=data)
                if attempt < max_retries:
                    self._sleep(float(2 ** attempt))
                    continue
                raise last_error

            # Non-retryable 4xx client errors (400, 404, 422, etc.)
            if status != 200:
                code = data.get("code") or f"HTTP{status}"
                msg = data.get("message") or response.text or f"HTTP {status}"
                raise NobitexAPIError(status, code, msg, payload=data)

            # HTTP 200 business-level failure check
            api_status = data.get("status")
            if api_status == "failed" or (check_status_ok and api_status is not None and api_status not in ("ok", "success")):
                code = data.get("code") or "BusinessError"
                msg = data.get("message") or "Nobitex API returned failed status"
                raise NobitexAPIError(200, code, msg, payload=data)

            return data

        raise last_error or NobitexAPIError(0, "UnknownError", "Request failed after retries")

    def fetch_trades_page(
        self,
        page: Optional[int] = 1,
        page_size: int = 500,
        src_currency: Optional[str] = None,
        dst_currency: Optional[str] = None,
        from_id: Optional[int] = None,
        trade_type: Optional[str] = None,
        trade_order: str = "desc",
    ) -> Dict[str, Any]:
        """
        Fetch a single page of trades from Nobitex (GET /market/trades/list).
        When from_id is provided, 'page' is omitted so page and fromId are never mixed.
        """
        params: Dict[str, Any] = {
            "pageSize": min(int(page_size), 500),
            "tradeOrder": trade_order,
        }
        if from_id is not None:
            params["fromId"] = int(from_id)
        elif page is not None:
            params["page"] = int(page)

        if src_currency and dst_currency:
            params["srcCurrency"] = src_currency
            params["dstCurrency"] = dst_currency
        if trade_type:
            params["tradeType"] = trade_type

        return self._request("GET", "/market/trades/list", params=params, auth=True, timeout=20.0)

    def fetch_all_trades(
        self,
        page_size: int = 500,
        max_pages: int = 100,
        src_currency: Optional[str] = None,
        dst_currency: Optional[str] = None,
        trade_type: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int, bool, str], None]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch all trades across pages (up to Nobitex's 180-day retention / 100-page cap).
        progress_callback signature: (page_num, total_trades_so_far, is_done, message)
        """
        all_trades: List[Dict[str, Any]] = []
        seen_ids: set[int] = set()
        page = 1

        while page <= max_pages:
            if progress_callback:
                progress_callback(page, len(all_trades), False, f"در حال دریافت صفحه {page}...")

            data = self.fetch_trades_page(
                page=page,
                page_size=page_size,
                src_currency=src_currency,
                dst_currency=dst_currency,
                trade_type=trade_type,
            )

            trades = data.get("trades", [])
            for t in trades:
                tid = t.get("id")
                if tid is None or tid not in seen_ids:
                    if tid is not None:
                        seen_ids.add(tid)
                    all_trades.append(t)

            has_next = data.get("hasNext", False)
            if not has_next or len(trades) == 0:
                break

            page += 1

        if progress_callback:
            progress_callback(page, len(all_trades), True, f"موفق: {len(all_trades)} معامله دریافت شد.")

        return all_trades

    def fetch_incremental_trades(
        self,
        since_id: int,
        page_size: int = 500,
        max_batches: int = 50,
        src_currency: Optional[str] = None,
        dst_currency: Optional[str] = None,
        trade_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Incrementally fetch all trades with id > since_id using ascending fromId cursor.
        Never sends 'page' and 'fromId' simultaneously.
        """
        effective_page_size = min(int(page_size), 500)
        cursor_id = int(since_id) + 1
        collected: List[Dict[str, Any]] = []
        seen_ids: set[int] = set()

        for _ in range(max_batches):
            data = self.fetch_trades_page(
                page=None,
                page_size=effective_page_size,
                src_currency=src_currency,
                dst_currency=dst_currency,
                from_id=cursor_id,
                trade_type=trade_type,
                trade_order="asc",
            )
            trades = data.get("trades", [])
            if not trades:
                break

            batch_max_id = -1
            new_in_batch = 0
            for t in trades:
                tid = t.get("id")
                if tid is not None:
                    tid_int = int(tid)
                    if tid_int > batch_max_id:
                        batch_max_id = tid_int
                    if tid_int in seen_ids:
                        continue
                    seen_ids.add(tid_int)
                collected.append(t)
                new_in_batch += 1

            has_next = data.get("hasNext")
            if has_next is False or len(trades) < effective_page_size or new_in_batch == 0 or batch_max_id < cursor_id:
                break

            cursor_id = batch_max_id + 1

        return collected

    def fetch_wallets(self, wallet_type: str = "spot") -> List[Dict[str, Any]]:
        """
        Fetch user wallets and their balances (POST /users/wallets/list).
        wallet_type: 'spot' (default) or 'margin'.
        """
        wtype = (wallet_type or "spot").strip().lower()
        body_dict = {"type": wtype} if wtype in ("spot", "margin") else None
        data = self._request("POST", "/users/wallets/list", body_dict=body_dict, auth=True, timeout=20.0)
        return data.get("wallets", [])

    def fetch_market_stats(
        self, src_currencies: Optional[List[str]] = None, dst_currency: str = "usdt"
    ) -> Dict[str, Any]:
        """Fetch real-time market statistics from Nobitex (public endpoint, no auth)."""
        params: Dict[str, Any] = {}
        if src_currencies:
            params["srcCurrency"] = ",".join(c.lower() for c in src_currencies)
            params["dstCurrency"] = dst_currency.lower()
        data = self._request("GET", "/market/stats", params=params, auth=False, timeout=15.0)
        return data.get("stats", {})

    def fetch_positions(
        self, status: str = "active", page: int = 1, page_size: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Fetch a single page of leveraged margin positions from /positions/list.
        status can be 'active' (open positions) or 'past' (closed/liquidated positions).
        """
        params = {"status": status, "page": int(page), "pageSize": min(int(page_size), 100)}
        data = self._request("GET", "/positions/list", params=params, auth=True, timeout=15.0)
        return data.get("positions", [])

    def fetch_all_positions(
        self,
        status: str = "past",
        page_size: int = 100,
        max_pages: int = 10,
        stop_on_known_ids: Optional[set[int]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Paginate through GET /positions/list up to max_pages.
        If stop_on_known_ids is provided (for status='past'), stops early once an entire
        page consists of already-known closed position IDs to conserve the 30 req/10 min quota.
        """
        effective_page_size = min(int(page_size), 100)
        all_positions: List[Dict[str, Any]] = []
        seen_ids: set[int] = set()

        for page in range(1, max_pages + 1):
            params = {"status": status, "page": page, "pageSize": effective_page_size}
            data = self._request("GET", "/positions/list", params=params, auth=True, timeout=15.0)
            positions = data.get("positions", [])
            if not positions:
                break

            page_ids: List[int] = []
            for p in positions:
                pid = p.get("id")
                if pid is not None:
                    pid_int = int(pid)
                    page_ids.append(pid_int)
                    if pid_int in seen_ids:
                        continue
                    seen_ids.add(pid_int)
                all_positions.append(p)

            if stop_on_known_ids and page_ids and all(pid in stop_on_known_ids for pid in page_ids):
                break

            has_next = data.get("hasNext")
            if has_next is False or len(positions) < effective_page_size:
                break

        return all_positions

    def fetch_position_status(self, position_id: int) -> Dict[str, Any]:
        """
        Fetch current status of a single margin position via GET /positions/{positionId}/status.
        Rate limit: 100 requests / 10 minutes.
        """
        pid = int(position_id)
        data = self._request("GET", f"/positions/{pid}/status", auth=True, timeout=15.0)
        pos = data.get("position") if isinstance(data.get("position"), dict) else data
        if "id" not in pos:
            pos = {**pos, "id": pid}
        return pos

    def fetch_orders_page(
        self,
        status: str = "all",
        page: Optional[int] = None,
        page_size: int = 100,
        from_id: Optional[int] = None,
        details: int = 2,
    ) -> Dict[str, Any]:
        """
        Fetch a single page of user orders from GET /market/orders/list.
        Per Nobitex docs, 'page' and 'fromId' MUST NOT be sent simultaneously.
        With from_id, pageSize up to 1000 is supported.
        """
        if page is not None and from_id is not None:
            raise ValueError("Cannot pass both 'page' and 'from_id' to /market/orders/list")

        params: Dict[str, Any] = {
            "status": status,
            "details": details,
        }
        if from_id is not None:
            params["fromId"] = int(from_id)
            params["pageSize"] = min(int(page_size), 1000)
        else:
            params["page"] = int(page if page is not None else 1)
            params["pageSize"] = min(int(page_size), 100)

        return self._request("GET", "/market/orders/list", params=params, auth=True, timeout=20.0)

    def fetch_all_orders(
        self, status: str = "all", page_size: int = 100, max_pages: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Fetch user orders with tradeType ('Spot' vs 'Margin') from /market/orders/list.
        Rate limiting and 429 retries are enforced automatically via _request().
        """
        effective_page_size = min(int(page_size), 100)
        all_orders: List[Dict[str, Any]] = []
        seen_ids: set[int] = set()

        for page in range(1, max_pages + 1):
            data = self.fetch_orders_page(
                status=status,
                page=page,
                page_size=effective_page_size,
                from_id=None,
                details=2,
            )
            orders = data.get("orders", [])
            if not orders:
                break
            for o in orders:
                oid = o.get("id")
                if oid is None or oid not in seen_ids:
                    if oid is not None:
                        seen_ids.add(oid)
                    all_orders.append(o)
            has_next = data.get("hasNext")
            if has_next is False or len(orders) < effective_page_size:
                break
        return all_orders

    def fetch_incremental_orders(
        self,
        since_id: int,
        status: str = "all",
        page_size: int = 1000,
        max_batches: int = 20,
        details: int = 2,
    ) -> List[Dict[str, Any]]:
        """
        Incrementally fetch orders with id > since_id using fromId (never mixing page & fromId).
        """
        effective_page_size = min(int(page_size), 1000)
        cursor_id = int(since_id) + 1
        collected: List[Dict[str, Any]] = []
        seen_ids: set[int] = set()

        for _ in range(max_batches):
            data = self.fetch_orders_page(
                status=status,
                page=None,
                page_size=effective_page_size,
                from_id=cursor_id,
                details=details,
            )
            orders = data.get("orders", [])
            if not orders:
                break

            batch_max_id = -1
            new_in_batch = 0
            for o in orders:
                oid = o.get("id")
                if oid is not None:
                    oid_int = int(oid)
                    if oid_int > batch_max_id:
                        batch_max_id = oid_int
                    if oid_int in seen_ids:
                        continue
                    seen_ids.add(oid_int)
                collected.append(o)
                new_in_batch += 1

            has_next = data.get("hasNext")
            if has_next is False or len(orders) < effective_page_size or new_in_batch == 0 or batch_max_id < cursor_id:
                break

            cursor_id = batch_max_id + 1

        return collected

    def fetch_udf_history(
        self,
        symbol: str,
        resolution: str,
        from_ts: int,
        to_ts: int,
        max_pages: int = 10,
        countback: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Fetch OHLCV candles from public GET /market/udf/history with 500-bar pagination.
        Loops page=1..max_pages, deduplicating and sorting bars ascending by timestamp 't'.
        Rate limit: 60 req/min (enforced via _request).
        """
        bars_by_ts: Dict[int, Tuple[float, float, float, float, float]] = {}
        last_status = "no_data"

        for page in range(1, max(1, int(max_pages)) + 1):
            params: Dict[str, Any] = {
                "symbol": symbol,
                "resolution": resolution,
                "from": int(from_ts),
                "to": int(to_ts),
                "page": page,
            }
            if countback is not None:
                params["countback"] = int(countback)

            data = self._request(
                "GET",
                "/market/udf/history",
                params=params,
                auth=False,
                timeout=12.0,
                check_status_ok=False,
            )

            s_val = data.get("s")
            if s_val != "ok":
                if page == 1:
                    last_status = s_val or "no_data"
                break

            last_status = "ok"
            t_arr = data.get("t", []) or []
            o_arr = data.get("o", []) or []
            h_arr = data.get("h", []) or []
            l_arr = data.get("l", []) or []
            c_arr = data.get("c", []) or []
            v_arr = data.get("v", []) or []

            if not t_arr:
                break

            new_in_page = 0
            for idx, ts in enumerate(t_arr):
                ts_int = int(ts)
                if ts_int not in bars_by_ts:
                    new_in_page += 1
                o_val = float(o_arr[idx]) if idx < len(o_arr) else 0.0
                h_val = float(h_arr[idx]) if idx < len(h_arr) else o_val
                l_val = float(l_arr[idx]) if idx < len(l_arr) else o_val
                c_val = float(c_arr[idx]) if idx < len(c_arr) else o_val
                v_val = float(v_arr[idx]) if idx < len(v_arr) and v_arr[idx] is not None else 0.0
                bars_by_ts[ts_int] = (o_val, h_val, l_val, c_val, v_val)

            if len(t_arr) < 500 or new_in_page == 0:
                break

        if not bars_by_ts:
            return {"s": last_status, "t": [], "o": [], "h": [], "l": [], "c": [], "v": []}

        sorted_ts = sorted(bars_by_ts.keys())
        return {
            "s": "ok",
            "t": sorted_ts,
            "o": [bars_by_ts[ts][0] for ts in sorted_ts],
            "h": [bars_by_ts[ts][1] for ts in sorted_ts],
            "l": [bars_by_ts[ts][2] for ts in sorted_ts],
            "c": [bars_by_ts[ts][3] for ts in sorted_ts],
            "v": [bars_by_ts[ts][4] for ts in sorted_ts],
        }

    def fetch_margin_markets(self, authenticated: bool = True) -> Dict[str, Any]:
        """
        Fetch margin market configurations (maxLeverage, fee rates, pool availability)
        from GET /margin/markets/list. Falls back to public call if keys are absent.
        """
        use_auth = bool(authenticated and self.api_key and self.secret)
        data = self._request("GET", "/margin/markets/list", auth=use_auth, timeout=15.0)
        markets = data.get("markets")
        return markets if isinstance(markets, dict) else {}

    def fetch_system_options(self) -> Dict[str, Any]:
        """
        Fetch Nobitex system options (precisions, min order amounts, features)
        from public GET /v2/options.
        """
        return self._request("GET", "/v2/options", auth=False, timeout=15.0)

    def fetch_user_profile(self) -> Dict[str, Any]:
        """
        Fetch authenticated user profile via GET /users/profile.
        Contains 'websocketAuthParam' used to subscribe to private Centrifugo channels.
        """
        data = self._request("GET", "/users/profile", auth=True, timeout=15.0)
        profile = data.get("profile")
        return profile if isinstance(profile, dict) else data

    def fetch_ws_token(self) -> str:
        """
        Fetch short-lived Centrifugo JWT token (1200s TTL) via GET /auth/ws/token/.
        Requires READ permission on API key.
        """
        data = self._request(
            "GET",
            "/auth/ws/token/",
            auth=True,
            timeout=15.0,
            check_status_ok=False,
        )
        token = data.get("token")
        if not token or not isinstance(token, str):
            raise NobitexAPIError(
                200,
                "MissingWSToken",
                "Nobitex /auth/ws/token/ did not return a token",
                payload=data,
            )
        return token

    def fetch_portfolio_daily_profit(self, monthly: bool = True) -> Dict[str, Any]:
        """
        Fetch Nobitex daily portfolio profit via POST /users/portfolio/last-week-daily-profit.
        Pass monthly=True for the last 30 days (otherwise last 7 days). Rate limit: 10 / 3 min.
        """
        body = {"monthly": True} if monthly else {}
        return self._request(
            "POST",
            "/users/portfolio/last-week-daily-profit",
            body_dict=body,
            auth=True,
            timeout=20.0,
        )

    def fetch_portfolio_daily_total_profit(self, monthly: bool = True) -> Dict[str, Any]:
        """
        Fetch Nobitex daily total portfolio profit via POST /users/portfolio/last-week-daily-total-profit.
        Pass monthly=True for the last 30 days (otherwise last 7 days). Rate limit: 10 / 3 min.
        """
        body = {"monthly": True} if monthly else {}
        return self._request(
            "POST",
            "/users/portfolio/last-week-daily-total-profit",
            body_dict=body,
            auth=True,
            timeout=20.0,
        )

    def fetch_deposits(
        self,
        page: int = 1,
        page_size: int = 100,
        wallet_id: Optional[int] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch a single page of user deposits via GET /users/wallets/deposits/list (READ permission).
        """
        params: Dict[str, Any] = {
            "page": int(page),
            "pageSize": min(int(page_size), 100),
        }
        if wallet_id is not None:
            params["wallet"] = int(wallet_id)
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        data = self._request("GET", "/users/wallets/deposits/list", params=params, auth=True, timeout=20.0)
        deposits = data.get("deposits", [])
        return deposits if isinstance(deposits, list) else []

    def fetch_withdrawals(
        self,
        page: int = 1,
        page_size: int = 100,
        wallet_id: Optional[int] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch a single page of user withdrawals via GET /users/wallets/withdraws/list (READ permission).
        """
        params: Dict[str, Any] = {
            "page": int(page),
            "pageSize": min(int(page_size), 100),
        }
        if wallet_id is not None:
            params["wallet"] = int(wallet_id)
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        data = self._request("GET", "/users/wallets/withdraws/list", params=params, auth=True, timeout=20.0)
        withdraws = data.get("withdraws", data.get("withdrawals", []))
        return withdraws if isinstance(withdraws, list) else []

    def fetch_all_deposits(self, page_size: int = 100, max_pages: int = 10) -> List[Dict[str, Any]]:
        """Paginate through user deposits safely up to max_pages."""
        eff_size = min(int(page_size), 100)
        collected: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for page in range(1, max(1, int(max_pages)) + 1):
            batch = self.fetch_deposits(page=page, page_size=eff_size)
            if not batch:
                break
            new_in_page = 0
            for item in batch:
                key = str(item.get("id") or item.get("txHash") or item.get("hash") or len(collected))
                if key in seen:
                    continue
                seen.add(key)
                collected.append(item)
                new_in_page += 1
            if len(batch) < eff_size or new_in_page == 0:
                break
        return collected

    def fetch_all_withdrawals(self, page_size: int = 100, max_pages: int = 10) -> List[Dict[str, Any]]:
        """Paginate through user withdrawals safely up to max_pages."""
        eff_size = min(int(page_size), 100)
        collected: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for page in range(1, max(1, int(max_pages)) + 1):
            batch = self.fetch_withdrawals(page=page, page_size=eff_size)
            if not batch:
                break
            new_in_page = 0
            for item in batch:
                key = str(item.get("id") or item.get("txHash") or item.get("hash") or len(collected))
                if key in seen:
                    continue
                seen.add(key)
                collected.append(item)
                new_in_page += 1
            if len(batch) < eff_size or new_in_page == 0:
                break
        return collected

    def cancel_order(self, order_id: int) -> Dict[str, Any]:
        """
        Cancel an open order via POST /market/orders/update-status.
        """
        body_dict = {"order": int(order_id), "status": "canceled"}
        return self._request(
            "POST",
            "/market/orders/update-status",
            body_dict=body_dict,
            auth=True,
            timeout=15.0,
            max_retries=1,
        )
