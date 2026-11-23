from __future__ import annotations

import base64
import threading
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from nob.nobitex_client import (
    NobitexAuthError,
    NobitexClient,
    NobitexRateLimitError,
    parse_private_key,
    sign_request,
)


def _valid_secret_b64() -> str:
    pk = Ed25519PrivateKey.generate()
    raw = pk.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return base64.urlsafe_b64encode(raw).decode("utf-8")


def test_parse_private_key_wraps_invalid_secret_in_auth_error():
    with pytest.raises(NobitexAuthError) as exc:
        parse_private_key("not-valid-base64-key!!!")
    assert exc.value.code == "InvalidSecret"


def test_sign_request_and_cached_private_key():
    secret = _valid_secret_b64()
    client = NobitexClient(api_key="k", secret=secret, sleep_fn=lambda _: None)
    key1 = client._get_private_key()
    key2 = client._get_private_key()
    assert key1 is key2
    sig, ts = sign_request(secret, "GET", "/market/stats", private_key=key1)
    assert sig and ts.isdigit()
    client.close()


def test_thread_local_sessions_isolated_and_closed():
    client = NobitexClient(api_key="", secret="", sleep_fn=lambda _: None)
    seen_ids = []

    def worker():
        seen_ids.append(id(client.session))

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(seen_ids) == 2
    assert seen_ids[0] != seen_ids[1]
    assert len(client._sessions) == 2
    client.close()
    assert len(client._sessions) == 0


def test_401_403_fail_fast_zero_retries():
    secret = _valid_secret_b64()
    sleeps = []
    client = NobitexClient(api_key="k", secret=secret, sleep_fn=lambda s: sleeps.append(s))
    mock_sess = MagicMock()
    resp = MagicMock()
    resp.status_code = 403
    resp.json.return_value = {"status": "failed", "code": "PermissionDenied", "message": "No TRADE"}
    mock_sess.request.return_value = resp
    client.session = mock_sess

    with pytest.raises(NobitexAuthError):
        client._request("POST", "/market/orders/update-status", body_dict={"order": 1}, max_retries=3)

    assert mock_sess.request.call_count == 1
    assert sleeps == []


def test_429_respects_backoff_and_raises_after_retries():
    sleeps = []
    client = NobitexClient(api_key="", secret="", sleep_fn=lambda s: sleeps.append(s))
    mock_sess = MagicMock()
    resp = MagicMock()
    resp.status_code = 429
    resp.headers = {}
    resp.json.return_value = {"status": "failed", "code": "TooManyRequests", "backOff": 2}
    mock_sess.request.return_value = resp
    client.session = mock_sess

    with pytest.raises(NobitexRateLimitError):
        client._request("GET", "/market/stats", auth=False, max_retries=2)

    assert mock_sess.request.call_count == 3
    assert len(sleeps) >= 2
