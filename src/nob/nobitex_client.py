import os
import time
import base64
import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
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
USER_AGENT = "TraderBot/NobAPI-1.0.0"


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
    def __init__(self, api_key: Optional[str] = None, secret: Optional[str] = None, base_url: str = DEFAULT_BASE_URL):
        if not api_key or not secret:
            api_key, secret = load_credentials()
        self.api_key = api_key
        self.secret = secret
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

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

        query_string = urlencode(params)
        full_path = f"/market/trades/list?{query_string}"

        sig, ts = sign_request(self.secret, "GET", full_path, "")
        headers = {
            "Nobitex-Key": self.api_key,
            "Nobitex-Signature": sig,
            "Nobitex-Timestamp": ts,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }

        url = f"{self.base_url}{full_path}"
        response = self.session.get(url, headers=headers, timeout=20)

        if response.status_code != 200:
            try:
                err_data = response.json()
                msg = err_data.get("message") or err_data.get("code") or str(err_data)
            except Exception:
                msg = response.text or f"HTTP {response.status_code}"
            raise RuntimeError(f"Nobitex API Error (HTTP {response.status_code}): {msg}")

        data = response.json()
        if data.get("status") == "failed":
            raise RuntimeError(f"Nobitex Error: {data.get('message', 'Unknown error')} (code: {data.get('code')})")

        return data

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
            # Respect rate limit (30 requests/minute max)
            time.sleep(2.0)

        if progress_callback:
            progress_callback(page, len(all_trades), True, f"موفق: {len(all_trades)} معامله دریافت شد.")

        return all_trades

    def fetch_wallets(self) -> List[Dict[str, Any]]:
        """Fetch all user wallets and their balances (POST /users/wallets/list)."""
        full_path = "/users/wallets/list"
        sig, ts = sign_request(self.secret, "POST", full_path, "")
        headers = {
            "Nobitex-Key": self.api_key,
            "Nobitex-Signature": sig,
            "Nobitex-Timestamp": ts,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        url = f"{self.base_url}{full_path}"
        response = self.session.post(url, headers=headers, timeout=20)
        if response.status_code != 200:
            raise RuntimeError(f"Nobitex Wallets Error (HTTP {response.status_code}): {response.text}")
        data = response.json()
        if data.get("status") != "ok":
            raise RuntimeError(f"Nobitex Wallets Error: {data.get('message', 'Unknown error')}")
        return data.get("wallets", [])

    def fetch_market_stats(
        self, src_currencies: Optional[List[str]] = None, dst_currency: str = "usdt"
    ) -> Dict[str, Any]:
        """Fetch real-time market statistics from Nobitex (public endpoint, no auth)."""
        url = f"{self.base_url}/market/stats"
        params = {}
        if src_currencies:
            params["srcCurrency"] = ",".join(c.lower() for c in src_currencies)
            params["dstCurrency"] = dst_currency.lower()
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        response = self.session.get(url, params=params, headers=headers, timeout=15)
        if response.status_code != 200:
            raise RuntimeError(f"Nobitex Market Stats Error (HTTP {response.status_code})")
        data = response.json()
        return data.get("stats", {})

    def fetch_positions(
        self, status: str = "active", page: int = 1, page_size: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Fetch leveraged margin positions from /positions/list.
        status can be 'active' (open positions) or 'past' (closed/liquidated positions).
        """
        full_path = f"/positions/list?status={status}&page={page}&pageSize={page_size}"
        sig, ts = sign_request(self.secret, "GET", full_path, "")
        headers = {
            "Nobitex-Key": self.api_key,
            "Nobitex-Signature": sig,
            "Nobitex-Timestamp": ts,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        url = f"{self.base_url}{full_path}"
        response = self.session.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            raise RuntimeError(f"Nobitex Positions Error (HTTP {response.status_code}): {response.text}")
        data = response.json()
        if data.get("status") != "ok":
            raise RuntimeError(f"Nobitex Positions Error: {data.get('message', 'Unknown error')}")
        return data.get("positions", [])

    def fetch_all_orders(
        self, status: str = "all", page_size: int = 100, max_pages: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Fetch user orders with tradeType ('Spot' vs 'Margin') from /market/orders/list.
        """
        all_orders: List[Dict[str, Any]] = []
        page = 1
        while page <= max_pages:
            full_path = f"/market/orders/list?status={status}&details=2&pageSize={page_size}&page={page}"
            sig, ts = sign_request(self.secret, "GET", full_path, "")
            headers = {
                "Nobitex-Key": self.api_key,
                "Nobitex-Signature": sig,
                "Nobitex-Timestamp": ts,
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            }
            url = f"{self.base_url}{full_path}"
            response = self.session.get(url, headers=headers, timeout=20)
            if response.status_code != 200:
                break
            data = response.json()
            orders = data.get("orders", [])
            if not orders:
                break
            all_orders.extend(orders)
            if len(orders) < page_size:
                break
            page += 1
            time.sleep(0.1)
        return all_orders
