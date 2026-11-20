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
                cutoff = now - window_sec
                while dq and dq[0] <= cutoff:
                    dq.popleft()

                if dq and min_spacing > 0:
                    since_last = now - dq[-1]
                    if since_last < min_spacing:
                        wait_sec = max(wait_sec, min_spacing - since_last)

                if len(dq) >= max_calls:
                    window_wait = (dq[0] + window_sec) - now
                    if window_wait > 0:
                        wait_sec = max(wait_sec, window_wait)

                if wait_sec <= 0.0:
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
        page: int = 1,
        page_size: int = 500,
        src_currency: Optional[str] = None,
        dst_currency: Optional[str] = None,
        from_id: Optional[int] = None,
        trade_type: Optional[str] = None,
        trade_order: str = "desc",
    ) -> Dict[str, Any]:
        """Fetch a single page of trades from Nobitex."""
        params: Dict[str, Any] = {
            "page": page,
            "pageSize": page_size,
            "tradeOrder": trade_order,
        }
        if src_currency and dst_currency:
            params["srcCurrency"] = src_currency
            params["dstCurrency"] = dst_currency
        if from_id is not None:
            params["fromId"] = from_id
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
        Fetch all trades across pages.
        progress_callback signature: (page_num, total_trades_so_far, is_done, message)
        """
        all_trades: List[Dict[str, Any]] = []
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
            all_trades.extend(trades)

            has_next = data.get("hasNext", False)
            if not has_next or len(trades) == 0:
                break

            page += 1

        if progress_callback:
            progress_callback(page, len(all_trades), True, f"موفق: {len(all_trades)} معامله دریافت شد.")

        return all_trades

    def fetch_wallets(self) -> List[Dict[str, Any]]:
        """Fetch all user wallets and their balances (POST /users/wallets/list)."""
        data = self._request("POST", "/users/wallets/list", auth=True, timeout=20.0)
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
        Fetch leveraged margin positions from /positions/list.
        status can be 'active' (open positions) or 'past' (closed/liquidated positions).
        """
        params = {"status": status, "page": page, "pageSize": page_size}
        data = self._request("GET", "/positions/list", params=params, auth=True, timeout=15.0)
        return data.get("positions", [])

    def fetch_all_orders(
        self, status: str = "all", page_size: int = 100, max_pages: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Fetch user orders with tradeType ('Spot' vs 'Margin') from /market/orders/list.
        Rate limiting and 429 retries are enforced automatically via _request().
        """
        all_orders: List[Dict[str, Any]] = []
        page = 1
        while page <= max_pages:
            params = {
                "status": status,
                "details": 2,
                "pageSize": page_size,
                "page": page,
            }
            data = self._request("GET", "/market/orders/list", params=params, auth=True, timeout=20.0)
            orders = data.get("orders", [])
            if not orders:
                break
            all_orders.extend(orders)
            if len(orders) < page_size:
                break
            page += 1
        return all_orders

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
