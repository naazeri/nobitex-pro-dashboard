# Execution Plan: Nobitex API Compliance, Bug Fixes & Architecture Upgrades (`nob-api`)

> **Companion Document:** `API_AUDIT_REPORT.md`  
> **Guardrails Document:** `DECISIONS.md` (ADR-001 through ADR-012 — strictly enforced)  
> **Design Principle:** Each phase is atomic, independently testable, and maps to 1 clean git commit. High-risk or architectural changes are isolated into dedicated phases; tightly coupled changes sharing the same module are grouped together.

---

## Phase Dependency Overview

```text
Phase 1 (DB WAL, Lifespan, IRST Timezone)
   │
   ├──▶ Phase 2 (NobitexClient Transport, Rate Limit, 429 backOff, Auth Fail-Fast)
   │       │
   │       ├──▶ Phase 3 (Incremental fromId Sync & Full Positions Pagination)
   │       ├──▶ Phase 6 (UDF 500-Candle Pagination, 7d Clamp, Bounded Cache)
   │       ├──▶ Phase 7 (Live /margin/markets/list & /v2/options Metadata)
   │       ├──▶ Phase 9 (Backend-Proxied Centrifugo WebSocket Bridge)
   │       ├──▶ Phase 10 (Portfolio / Deposits / Withdrawals & True Equity TWR)
   │       └──▶ Phase 11 (Margin Close / Edit Collateral / Bulk Cancel / Profile)
   │
   ├──▶ Phase 4 (Surgical Fixes: Open Orders Snapshot, Asset Modal, Calendar Math)
   └──▶ Phase 5 (Portfolio WAC Date Replay, -RLS Markets, Margin Wallets)
           │
           └──▶ Phase 8 (float → Decimal Precision Migration)
```

### Summary Table

| Phase | Title | Audit IDs | Grouping Strategy | Risk |
| :--- | :--- | :--- | :--- | :--- |
| **Phase 1** ✅ | Core Infra: SQLite WAL, Lifespan & IRST Timezone | `DB-01`, `BUG-04` (core) | Grouped (foundational infra) | Low (Done) |
| **Phase 2** ✅ | `NobitexClient` Transport, Rate Limiting & Error Mapping | `API-01`, `API-05` | Grouped (same transport layer) | Medium (Done) |
| **Phase 3** ✅ | Incremental Sync (`fromId`) & Full Positions Pagination | `API-02`, `API-03` | Grouped (same sync pipeline) | Medium (Done) |
| **Phase 4** ✅ | Surgical Fixes: Open Orders & Calendar Math | `BUG-01`, `BUG-02` | Grouped (small isolated fixes) | Low (Done) |
| **Phase 5** ✅ | Spot Portfolio WAC Replay, `-RLS` Markets & Margin Wallets | `BUG-03`, `BUG-04` (SQL) | Isolated (financial core) | Medium (Done) |
| **Phase 6** ✅ | Chart UDF 500-Bar Pagination, 7-Day Clamp & Cache | `API-04` | Isolated (charting subsystem) | Low (Done) |
| **Phase 7** ✅ | Dynamic Market Metadata (`/margin/markets/list`, `/v2/options`) | `OPT-01` | Isolated (metadata layer) | Low (Done) |
| **Phase 8** | Monetary Precision Migration (`float` → `Decimal`) | `OPT-02` | Isolated (regression-sensitive) | Medium |
| **Phase 9** | Real-Time WebSocket Bridge (Backend Centrifugo → Local WS) | `OPT-03` | Isolated (new subsystem) | High |
| **Phase 10** | True Equity Curve & Cashflow Reconciliation | `OPT-04` | Isolated (new tables + TWR math) | Medium |
| **Phase 11** | Margin Position Actions, Bulk Cancel & User Tier UI | `OPT-05` | Isolated (optional `TRADE` UX) | Medium |

---

## Phase 1: Core Infrastructure — SQLite Concurrency, Lifespan & IRST Timezone ✅ (Completed)
**Covers:** `DB-01`, `BUG-04` (foundation)  
**Why grouped:** All three are low-risk foundational improvements that eliminate SQLite locking, deprecated FastAPI startup hooks, and OS-timezone drift before touching API or PnL logic.

### Files to Create / Modify
- **Create:** `src/nob/time_utils.py`
- **Modify:** `src/nob/db.py`, `src/nob/server.py`, `src/nob/pnl.py`

### Tasks
- [x] Create `src/nob/time_utils.py` defining:
  - `IRST = timezone(timedelta(hours=3, minutes=30))` (strict ADR-010, zero DST dependence).
  - `now_irst() -> datetime`, `today_irst_str() -> str`, `ts_to_irst_date_str(ts: int | float) -> str`, `iso_to_irst_date_str(iso_str: str) -> str`.
  - `irst_date_bounds_to_utc_iso(from_date: str | None, to_date: str | None) -> tuple[str | None, str | None]` converting Tehran day boundaries (`00:00:00+03:30` / `23:59:59.999999+03:30`) to UTC ISO strings.
- [x] In `src/nob/db.py`:
  - Update `get_db_connection()` to pass `timeout=30.0` and execute `PRAGMA journal_mode=WAL;`, `PRAGMA busy_timeout=30000;`, `PRAGMA synchronous=NORMAL;`.
  - Add a module-level `_initialized_dbs: set[Path] = set()` guard inside `init_db(db_path, force=False)` so DDL/migrations run once per DB path instead of on every query.
  - Change `upsert_trades()` from `INSERT OR REPLACE` to `INSERT INTO trades (...) VALUES (...) ON CONFLICT(id) DO UPDATE SET ...` to preserve `created_at` (and prevent downgrading existing `Margin` trades back to `Spot`).
  - Replace naive `datetime.now().isoformat()` timestamps with `now_irst().isoformat()`.
- [x] In `src/nob/server.py`:
  - Replace `@app.on_event("startup")` with FastAPI `lifespan` async context manager (`init_db()` on startup, shared client cleanup on shutdown).
  - Replace `datetime.fromtimestamp(ts)` (line 232) and `datetime.now()` (lines 251, 462, 813) with `time_utils` helpers.
- [x] In `src/nob/pnl.py`:
  - Replace `TEHRAN_TZ = zoneinfo.ZoneInfo("Asia/Tehran")` with `IRST` imported from `nob.time_utils`.

### Verification & Acceptance Criteria
- [x] Server syntax and `lifespan` migration verified (no `@app.on_event` usage remains).
- [x] `data/portfolio.db` verified in `wal` mode with `busy_timeout=30000`.
- [x] Concurrent multi-threaded reads and writes under WAL passed with zero `database is locked` errors.
- [x] Unit test confirmed `irst_date_bounds_to_utc_iso("2026-11-10", "2026-11-10")` maps to `2026-11-09T20:30:00+00:00` .. `2026-11-10T20:29:59.999999+00:00` and matches Nobitex microsecond timestamps lexicographically.

---

## Phase 2: `NobitexClient` Transport Hardening, Rate Limiting & Error Mapping ✅ (Completed)
**Covers:** `API-01`, `API-05`  
**Why grouped:** Both live inside the HTTP transport and error-handling boundary of `NobitexClient` and its FastAPI callers.

### Files to Modify
- `src/nob/nobitex_client.py`
- `src/nob/server.py`

### Tasks
- [x] Define typed exceptions in `src/nob/nobitex_client.py`:
  - `NobitexAPIError(status_code, code, message, payload=None, backoff=None)`
  - `NobitexAuthError(NobitexAPIError)` (for `401` / `403` / missing credentials)
  - `NobitexRateLimitError(NobitexAPIError)` (for exhausted `429`)
- [x] Make credentials optional in `NobitexClient.__init__(api_key=None, secret=None, base_url=DEFAULT_BASE_URL, require_auth=False)`:
  - Attempt `load_credentials()` silently if not provided; raise `NobitexAuthError(401, "MissingCredentials")` inside `_request(..., auth=True)` when keys are missing.
  - Add `close()` and context manager support (`__enter__`/`__exit__`) to close `self.session`.
- [x] Implement thread-safe per-endpoint sliding-window + min-spacing rate throttler (`_EndpointRateLimiter`) using `threading.Lock` (releasing lock during sleep):
  - `/market/trades/list`: 28/60s, min `2.05s` (official: 30/min)
  - `/market/orders/list`: 28/60s, min `2.05s` (official: 30/min)
  - `/positions/list`: 28/600s, min `0.5s` (official: 30/10 min)
  - `/positions/{id}/status`: 95/600s, min `0.2s` (official: 100/10 min)
  - `/market/stats`: 18/60s, min `3.1s` (official: 20/min)
  - `/users/wallets/list`: 18/60s, min `0.5s` (official: 20/min)
  - `/market/udf/history`: 55/60s, min `1.05s` (official: 60/min)
- [x] Implement centralized `_request(method, path, params=None, body_dict=None, auth=True, timeout=20, max_retries=3)`:
  - Always set `User-Agent: TraderBot/NobAPI-2.1.0` and `Accept: application/json`.
  - Sign `timestamp + METHOD + full_path + raw_body` fresh on **every attempt** (including retries, so `Nobitex-Timestamp` never drifts >30s).
  - On `HTTP 429`: read `backOff` from JSON (default `5`, clamped `1..60`), sleep `backOff + 0.5`s, retry up to `max_retries`.
  - On `HTTP 401` or `403`: **fail fast immediately (0 retries)** to avoid the 30-minute IP ban after 100 failed auths.
  - On `HTTP >= 500` or `requests.RequestException`: exponential backoff (`1s`, `2s`, `4s`).
  - On `HTTP 200` with `data.get("status") == "failed"`: raise `NobitexAPIError(200, data.get("code"), data.get("message"), data)`.
- [x] Refactor all existing `NobitexClient` methods (`fetch_trades_page`, `fetch_wallets`, `fetch_market_stats`, `fetch_positions`, `fetch_all_orders`, `cancel_order`) to route through `_request()`.
- [x] In `src/nob/server.py`:
  - Use a shared singleton `NobitexClient` (`get_nobitex_client()`) managed by `lifespan` (closed on shutdown).
  - Add a thread-safe 5-second cooldown cache on `POST /api/prices/refresh` to respect `/market/stats` 20/min limit.
  - In `api_cancel_order`: map `NobitexAuthError` → `403`, `NobitexRateLimitError` → `429`, `NobitexAPIError` → `4xx/422` returning structured JSON (`code`, `message`, `detail` string compatible with `index.html:4268`).

### Verification & Acceptance Criteria
- [x] Unit tests with mocked `requests.Session` verified:
  - `429` with `backOff` sleeps `backOff + 0.5`s (clamped `1..60`) and succeeds on retry.
  - `401`/`403` raises `NobitexAuthError` with **1** call (0 retries).
  - `NobitexClient(require_auth=False)` without credentials calls `fetch_market_stats()` (public) and fails fast with 0 network calls on `fetch_wallets()` (private).
  - `fetch_all_orders()` enforces 30 req/min spacing, retries transient 5xx/network errors with exponential backoff, and no longer silently `break`s on error.

---

## Phase 3: Sync Pipeline — Incremental `fromId` Sync & Full Positions Pagination ✅ (Completed)
**Covers:** `API-02`, `API-03`  
**Why grouped:** Both modify the pagination and synchronization loop across `nobitex_client.py`, `db.py`, and `server.py:perform_sync`.

### Files to Modify
- `src/nob/db.py`
- `src/nob/nobitex_client.py`
- `src/nob/server.py`

### Tasks
- [x] In `src/nob/db.py`, add helper queries:
  - `get_max_trade_id() -> Optional[int]`
  - `get_max_order_id() -> Optional[int]`
  - `get_known_closed_position_ids() -> set[int]`
  - `get_active_position_ids() -> set[int]`
- [x] In `src/nob/nobitex_client.py`:
  - Fix `fetch_trades_page()`: **omit `page`** whenever `from_id is not None` (never send both).
  - Add `fetch_orders_page(status="all", page=None, page_size=100, from_id=None, details=2)`: enforce mutual exclusion of `page` and `from_id`; when `from_id is not None`, allow `page_size` up to `1000`.
  - Add `fetch_incremental_trades(since_id)` and `fetch_incremental_orders(since_id)` that fetch only new records (`fromId = since_id + 1`).
  - Add `fetch_position_status(position_id: int)` calling `GET /positions/{position_id}/status` (100 req/10 min).
  - Add `fetch_all_positions(status="past", page_size=100, max_pages=10, stop_on_known_ids=None)`:
    - Paginate until `not data.get("hasNext")` or `len(positions) < page_size` or `page > max_pages`.
    - If `stop_on_known_ids` is provided and all IDs on the current page are already in `stop_on_known_ids`, break early to conserve the 30 req/10 min quota.
- [x] In `src/nob/server.py`:
  - Update `POST /api/sync` to accept optional `full: bool = False`.
  - In `perform_sync(full=False)`:
    - Default incremental mode uses `fromId = max_id + 1` for orders and trades (plus `status="open"` orders).
    - Reconcile stale `Open` positions: compare DB `get_active_position_ids()` against live `active_pos` IDs; for any missing ID, call `fetch_position_status(pid)` and upsert the updated closed/liquidated state.
    - Record permission errors (`NobitexAuthError` on `/positions/list`) cleanly in `sync_status["warnings"]` without aborting spot sync.

### Verification & Acceptance Criteria
- [x] Running `/api/sync` twice in a row performs minimal API calls on the second run (1 call for trades, 1 call for orders, 1 call for active positions).
- [x] Unit test verifies `page` is never present in the query string when `from_id` is passed.
- [x] Unit test verifies a previously `Open` position in SQLite transitions to `Closed`/`Liquidated` when absent from live `status=active`.

---

## Phase 4: Surgical Bug Fixes — Open Orders State & Calendar Math ✅ (Completed)
**Covers:** `BUG-01`, `BUG-02`  
**Why grouped:** Four small, self-contained, zero-risk fixes that immediately fix broken UI numbers and ghost open orders.

### Files to Modify
- `src/nob/db.py`
- `src/nob/server.py`
- `src/nob/pnl.py`
- `src/nob/static/index.html`

### Tasks
- [x] **Fix `BUG-01a` (`db.py`):** Update `get_open_orders()` query to `WHERE status IN ('Active', 'New', 'Inactive')`.
- [x] **Fix `BUG-01b` (`db.py` & `server.py`):** Add `reconcile_open_orders(live_orders)` in `db.py`:
  - Upsert all `live_orders`.
  - Any DB order currently in `('Active', 'New', 'Inactive')` whose `id` is NOT in `{o['id'] for o in live_orders}` is updated to `status = 'Closed'` (even when `live_orders == []`).
  - Call `reconcile_open_orders(raw_orders)` inside `api_get_open_orders()` whenever `source == "live"`.
- [x] **Fix `BUG-01c` (`index.html:1188`):** Change `formatCurrency(ord.total)` to `formatTradeTotal(ord.total_price, ord.market)` in the Asset Details modal open orders table.
- [x] **Fix `BUG-02a` (`pnl.py:601-613`):** Capture `entry_avg_cost = avg_cost` *before* the `if current_qty <= 1e-9: avg_cost = 0.0` reset, and compute `pnl_pct` using `entry_avg_cost`.
- [x] **Fix `BUG-02b` (`pnl.py:641`):** Fix futures daily volume from `round(entry_price * collateral, 2)` to `round(collateral * float(pos.get("leverage", 1.0) or 1.0), 2)` for USDT positions.

### Verification & Acceptance Criteria
- [x] Selling 100% of a spot holding shows the true non-zero `pnl_pct` in `/api/analytics/pnl-calendar` Day Inspector.
- [x] Closing a `$100` collateral `5x` BTC margin position adds `$500` (not `$9,000,000`) to `volume_usdt` in the calendar.
- [x] Canceling an order externally on Nobitex removes it from SQLite fallback when `/api/orders/open?refresh=true` returns `[]`.
- [x] Asset Details modal displays exact order total value instead of `$0.00`.

---

## Phase 5: Spot Portfolio Accounting — WAC Replay, `-RLS` Markets & Margin Wallets ✅ (Completed)
**Covers:** `BUG-03`, `BUG-04` (SQL date boundaries)  
**Why isolated:** Touches core Spot Portfolio accounting (`calculate_portfolio`), DB schema (`wallet_snapshots`), and SQL date filtering. Needs focused numerical validation against existing data.

### Files to Modify
- `src/nob/nobitex_client.py`
- `src/nob/db.py`
- `src/nob/pnl.py`
- `src/nob/server.py`

### Tasks
- [x] **Date-Range WAC Replay (`server.py`, `pnl.py`):**
  - In `api_get_portfolio()`, load **all** chronological spot trades (`get_all_crypto_trades_chronological(from_date=None, to_date=None)`) and pass `from_date` / `to_date` into `calculate_portfolio()`.
  - Inside `calculate_portfolio()`, replay ALL trades from history start to build true `current_qty` and `avg_cost`, but only accumulate `realized_pnl`, `asset_fees_usdt`, `total_bought_*`, `total_sold_*`, `trades_count`, and `trades_timeline` for trades whose IRST date falls inside `[from_date, to_date]`.
- [x] **Consistent IRST SQL Date Filtering (`db.py`):**
  - In `get_trades()` and `get_all_crypto_trades_chronological()`, convert `from_date` and `to_date` via `irst_date_bounds_to_utc_iso()` before comparing against UTC ISO `timestamp` column.
- [x] **Rial (`-RLS`) Spot Market Support (`pnl.py`):**
  - In `calculate_portfolio()`, handle both `quote == "USDT"` and `quote in ("RLS", "IRR", "IRT")` by converting RLS prices/totals/fees to USDT using `market_stats["usdt-rls"]["latest"]` (flagging approximate conversion if historical rate is unavailable, or storing a `usdt_rls` rate lookup).
- [x] **Margin Wallet Sync (`nobitex_client.py`, `db.py`, `server.py`, `pnl.py`):**
  - Update `NobitexClient.fetch_wallets(wallet_type: str = "spot")` to send JSON body `{"type": wallet_type}` to `POST /users/wallets/list`.
  - Migrate `wallet_snapshots` table (additive migration) to include `wallet_type TEXT DEFAULT 'spot'` with composite primary key `(currency, wallet_type)`.
  - Sync both `"spot"` and `"margin"` wallets in `perform_sync()`, exposing `margin_usdt_cash` in portfolio summary without double-counting active position collateral.

### ADR Guardrails
- Strictly preserve **ADR-001** (no hardcoded dates), **ADR-002** (`net_amount`), **ADR-003** (True Net PnL), and **ADR-010** (IRST).

### Verification & Acceptance Criteria
- [x] Selecting `30D` on Spot Portfolio preserves accurate `avg_cost` and `unrealized_pnl` for coins bought >30 days ago, while scoping `realized_pnl` and `fees` to the 30-day window.
- [x] A trade at `01:00 IRST` (`21:30 UTC` previous day) appears on the exact same date in Trades Table, Spot Portfolio, and PnL Calendar.
- [x] Crypto purchased in `-RLS` markets appears with valid cost basis and PnL in Spot Portfolio.

---

## Phase 6: Charting UDF Pagination, 7-Day Intraday Clamp & Bounded Cache ✅ (Completed)
**Covers:** `API-04`  
**Why isolated:** Self-contained within the OHLC/charting subsystem; ensures TradingView ADR-005 & ADR-006 stay intact.

### Files to Modify
- `src/nob/nobitex_client.py`
- `src/nob/server.py`

### Tasks
- [x] Add `NobitexClient.fetch_udf_history(symbol, resolution, from_ts, to_ts, max_pages=10)` using `_request(..., auth=False)`:
  - Loop `page=1..max_pages` (or chunk time windows so each request spans `<= 500` bars), merging arrays `t, o, h, l, c, v` and deduplicating/sorting by `t`.
  - Stop when `s != "ok"` or returned bar count `< 500`.
- [x] In `src/nob/server.py`:
  - Route both `api_get_candles()` and `sync_btc_history_if_needed()` through `client.fetch_udf_history(...)` (removing raw `urllib.request` and `Mozilla/5.0` headers).
  - For intraday resolutions `1`, `5`, `15`, `30`, clamp default `from_ts` to `max(from_ts, now - 7 * 86400)` per Nobitex Mehr 1405 changelog.
  - Replace unbounded `_candle_cache` dict with a bounded LRU/TTL cache (`MAX_CANDLE_CACHE_ENTRIES = 256`).

### ADR Guardrails
- Preserve **ADR-005** (URL symbol sync) and **ADR-006** (`1W`/`W` normalization & `{ noData: true }` termination).

### Verification & Acceptance Criteria
- [x] Loading `BTCUSDT` on `1W` returns >100 weekly candles (>500 daily bars fetched via pagination) without freezing.
- [x] Loading `15m` and `30m` loads cleanly within the 7-day window and terminates pagination cleanly.
- [x] Cache size never exceeds 256 entries under repeated symbol/timeframe switching.

---

## Phase 7: Dynamic Market Metadata (`/margin/markets/list` & `/v2/options`) ✅ (Completed)
**Covers:** `OPT-01`  
**Why isolated:** Replaces static hardcoded maps with live, cached Nobitex exchange configuration.

### Files to Modify
- `src/nob/nobitex_client.py`
- `src/nob/db.py`
- `src/nob/server.py`
- `src/nob/pnl.py`

### Tasks
- [x] In `NobitexClient`, add:
  - `fetch_margin_markets(authenticated: bool = True)` → `GET /margin/markets/list`
  - `fetch_system_options()` → `GET /v2/options`
- [x] In `db.py`, store cached JSON blobs (`margin_markets_cache`, `system_options_cache` with `updated_at` in `metadata` table, 12h TTL) and refresh during `perform_sync()`.
- [x] In `server.py:api_get_markets`: replace hardcoded `leverage_map` with live `maxLeverage` from `margin_markets_cache` (falling back to existing defaults if offline/unpopulated) and include `price_precision` / `amount_precision` from `system_options_cache`.
- [x] In `pnl.py:calculate_positions_summary`: use live `buyPositionFeeRate` / `sellPositionFeeRate` and renewal rate from `margin_markets_cache` when trade-level fee matching falls back to estimation, keeping `0.0008` / `0.00125` as safe fallbacks when fields are null/absent.

### Verification & Acceptance Criteria
- [x] `/api/markets` returns live `max_leverage` matching Nobitex's `/margin/markets/list` response.
- [x] App still works 100% offline when cache is empty (graceful fallback).

---

## Phase 8: Monetary Precision Migration (`float` → `Decimal`)
**Covers:** `OPT-02`  
**Why isolated:** Pure internal precision refactor; isolating it makes regression testing against existing `data/portfolio.db` exact and unambiguous.

### Files to Modify
- `src/nob/pnl.py`

### Tasks
- [ ] Create helper `D(val, default="0") -> Decimal` safely parsing strings/numbers via `Decimal(str(val))` (handling `None`, `""`, `NaN`).
- [ ] Convert WAC (`current_qty`, `avg_cost`, `net_amount`, `realized_pnl`, `unrealized_pnl`, fees) in `calculate_portfolio()` and `calculate_daily_pnl_calendar()` to `Decimal` arithmetic.
- [ ] Convert margin fee and True Net PnL arithmetic in `calculate_positions_summary()` to `Decimal`.
- [ ] Quantize/convert back to `float` only at the final JSON response dictionary assembly so API contracts remain 100% backward-compatible.

### Verification & Acceptance Criteria
- [ ] Snapshot-compare JSON outputs of `/api/portfolio`, `/api/positions`, and `/api/analytics/pnl-calendar` before and after migration on `data/portfolio.db` — confirm zero unexpected deltas and improved precision on micro-priced tokens (`SHIB`, `PEPE`, `DOGS`).

---

## Phase 9: Real-Time WebSocket Proxy (Backend Centrifugo Client → Local WS Bridge)
**Covers:** `OPT-03`  
**Why isolated:** Major new real-time subsystem; isolating it protects existing REST functionality and guarantees fallback to REST polling if WS disconnects.

### Files to Create / Modify
- **Create:** `src/nob/ws_bridge.py`
- **Modify:** `pyproject.toml` (add `websockets` if not already installed via `uvicorn[standard]`), `src/nob/nobitex_client.py`, `src/nob/server.py`, `src/nob/static/index.html`

### Tasks
- [ ] In `NobitexClient`, add:
  - `fetch_user_profile()` → `GET /users/profile` (extract `websocketAuthParam`).
  - `fetch_ws_token()` → `GET /auth/ws/token/` (JWT with 1200s TTL).
- [ ] Build `src/nob/ws_bridge.py` (`NobitexWSBridge`):
  - Runs as an `asyncio` background task started/stopped in FastAPI `lifespan`.
  - Connects to `wss://ws.nobitex.ir/connection/websocket` (plain Centrifugo JSON protocol; respond to empty `{}` ping with `{}` within 20s; do NOT enable `delta: 'fossil'` unless using an official Fossil decoder).
  - Subscribes to `public:market-stats-all` (updates in-memory prices + `mark` prices for Liquidation Risk Radar) and on-demand `public:candle-{SYMBOL}-{RESOLUTION}`.
  - If API keys are present, refreshes JWT every ~1000s and subscribes to `private:orders#{websocketAuthParam}` and `private:trades#{websocketAuthParam}` (using `time` ms field, NOT deprecated `timestamp`), immediately upserting fills/order updates into SQLite.
  - Exposes local FastAPI WebSocket endpoint `ws://127.0.0.1:8080/ws` to broadcast normalized events to connected Vue 3 tabs.
- [ ] In `src/nob/static/index.html`:
  - Connect to `ws://127.0.0.1:8080/ws` (100% local, honors ADR-012 and avoids Nobitex browser `Origin` 403 block).
  - Update spot prices, open orders, and Liquidation Risk Radar live from WS events; pause REST price polling while WS is connected (fallback to polling if WS drops).
  - Wire TradingView `subscribeBars` / `unsubscribeBars` to local WS candle updates.

### Verification & Acceptance Criteria
- [ ] Live prices and `mark` price update in UI without triggering `POST /api/prices/refresh`.
- [ ] Active TradingView candle updates smoothly every ~10s.
- [ ] Disconnecting internet gracefully falls back to offline local SQLite mode without crashing the server.

---

## Phase 10: True Historical Equity Curve & Cashflow Reconciliation
**Covers:** `OPT-04`  
**Why isolated:** Introduces new DB tables and changes the equity curve model to separate deposits/withdrawals from trading PnL.

### Files to Modify
- `src/nob/nobitex_client.py`
- `src/nob/db.py`
- `src/nob/pnl.py`
- `src/nob/server.py`

### Tasks
- [ ] In `NobitexClient`, add:
  - `fetch_portfolio_daily_profit(monthly=True)` → `POST /users/portfolio/last-week-daily-profit`
  - `fetch_portfolio_daily_total_profit(monthly=True)` → `POST /users/portfolio/last-week-daily-total-profit`
  - `fetch_deposits(page=1, page_size=100)` → `GET /users/wallets/deposits/list`
  - `fetch_withdrawals(page=1, page_size=100)` → `GET /users/wallets/withdraws/list`
- [ ] In `db.py`, add tables `cashflows (id, flow_type, currency, amount, tx_hash, status, timestamp)` and `daily_equity_snapshots (date PRIMARY KEY, total_equity_usdt, net_cashflow_usdt, source)`.
- [ ] In `pnl.py:calculate_equity_curve()`:
  - Incorporate `net_cashflow_usdt` to compute **Time-Weighted Return (TWR)** so external deposits/withdrawals do not create fake PnL spikes/drops.
  - Distinguish externally deposited coins from missing trade history in Spot Portfolio.

### Verification & Acceptance Criteria
- [ ] Depositing `$1,000` USDT increases `portfolio_equity` by `$1,000` while keeping `cumulative_return_pct` and `net_profit` unchanged.

---

## Phase 11: Advanced Margin Actions, Bulk Cancel & Account Tier Badge
**Covers:** `OPT-05`  
**Why isolated:** Optional interactive trading actions requiring `TRADE` permission (must remain cleanly gated for `READ`-only users).

### Files to Modify
- `src/nob/nobitex_client.py`
- `src/nob/server.py`
- `src/nob/static/index.html`
- `src/nob/static/i18n.js`

### Tasks
- [ ] Add `NobitexClient` methods:
  - `close_position(position_id, execution, amount, price=None, stop_price=None, stop_limit_price=None)` → `POST /positions/{positionId}/close`
  - `edit_position_collateral(position_id, collateral)` → `POST /positions/{positionId}/edit-collateral`
  - `cancel_old_orders(hours=None, execution=None, trade_type=None, src_currency=None, dst_currency=None)` → `POST /market/orders/cancel-old`
  - `fetch_user_limitations()` → `POST /users/limitations`
- [ ] Expose FastAPI endpoints (`/api/positions/{id}/close`, `/api/positions/{id}/edit-collateral`, `/api/orders/cancel-old`, `/api/user/profile`) with strict Pydantic validation and `NobitexAuthError` → `403` handling.
- [ ] Add confirmation modals in `index.html` + bilingual strings in `i18n.js` for:
  - "Add Collateral" quick button on Danger/Warning positions in Liquidation Risk Radar.
  - "Close Position" button on active positions.
  - "Cancel All Filtered Orders" button in Open Orders tab.
  - User VIP tier & API key permission badge in the top header.

### Verification & Acceptance Criteria
- [ ] All mutating actions require explicit confirmation modal before firing.
- [ ] Using a `READ`-only API key displays a clear bilingual message explaining `TRADE` permission is required, with zero crashes or retries.
