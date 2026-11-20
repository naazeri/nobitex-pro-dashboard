# Nobitex API Audit & Engineering Improvement Report (`nob-api`)

> **Target Audience:** AI Coding Agents & Maintainers  
> **Reference Documentation:** `https://apidocs.nobitex.ir/` (v2 REST API & Centrifugo WebSocket)  
> **Audited Codebase:** `src/nob/nobitex_client.py`, `src/nob/server.py`, `src/nob/pnl.py`, `src/nob/db.py`, `src/nob/export_csv.py`, `src/nob/static/index.html`  
> **Audit Date:** 2026-11-19  
> **Resolution Status:** ✅ **All 15 findings (`API-01..05`, `BUG-01..04`, `DB-01`, `OPT-01..05`) implemented & verified across Phases 1–11 (see `IMPLEMENTATION_PLAN.md`).**  

---

## 0. Mandatory Project Guardrails (From `DECISIONS.md`)

Any AI agent implementing fixes from this report **MUST NOT** violate the following accepted ADRs:

- **ADR-001:** Never reintroduce hardcoded date cutoffs (e.g., `2026-10-17`). All date filters must be user-driven and parametric.
- **ADR-002:** `net_amount = ROUND(MAX(0.0, amount - fee), 8)` applies **only** to `buy` trades; `sell` trades must keep `net_amount = NULL` (displayed as `—`).
- **ADR-003:** All displayed PnL values must remain **True Net PnL** (after buy/sell fees, margin open/close/extension fees, and `pool_pnl_share`).
- **ADR-004 / ADR-012:** Keep the app **100% offline-first** on the frontend. Never add external CDN URLs (`unpkg`, `jsdelivr`, `fonts.googleapis.com`). Any new JS libraries (e.g., `centrifuge` if used) or external API/WebSocket connections must either be vendored locally or proxied through the local FastAPI backend (`127.0.0.1:8080`).
- **ADR-005 / ADR-006:** Preserve TradingView `load_last_chart: false`, `onChartReady` symbol sync, `1W`/`W` normalization, and `{ noData: true }` pagination termination.
- **ADR-010:** All calendar day boundaries and displayed timestamps must strictly use Iran Standard Time (**IRST = UTC+03:30, no DST**).
- **ADR-011:** Preserve `.bidi-num` and Canvas 2D LTR direction overrides.

---

## 1. Master Issue & Opportunity Matrix

| ID | Category | Severity | Affected File(s) & Lines | Summary |
| :--- | :--- | :--- | :--- | :--- |
| `API-01` | Rate Limit & Retry | **P0 - Critical** | `nobitex_client.py:97-288`, `server.py:215-239, 570-669` | Zero `HTTP 429` (`backOff`) handling or retry logic; `fetch_all_orders` sleeps `0.1s` (600 req/min vs 30 req/min limit), risking a 2-minute global token ban. |
| `API-02` | Pagination | **P0 - Critical** | `nobitex_client.py:219-238`, `server.py:192-195, 424-426` | `fetch_positions()` only fetches `page=1`; users with >100 past positions silently lose history, and stale `Open` positions can get stuck in SQLite. |
| `API-03` | Sync Efficiency | **P0 - Critical** | `nobitex_client.py:97-185, 240-267`, `server.py:413-433` | Full re-sync from `page=1` every time instead of incremental `fromId` sync; `fetch_trades_page` allows sending `page` + `fromId` together; orders capped at 20 pages causing Margin trades to be misclassified as Spot. |
| `BUG-01` | Open Orders State | **P0 - Critical** | `server.py:711-715`, `db.py:236-247`, `index.html:1188` | Filled/canceled orders remain stuck as `Active` in SQLite; `Inactive`/`New` stop orders ignored; Asset Details modal reads `ord.total` instead of `ord.total_price`. |
| `API-04` | OHLC / UDF Limits | **P1 - High** | `server.py:215-239, 570-669` | `/market/udf/history` caps responses at **500 candles/request** (requires `page` param for 1W/1H ranges) and limits 1m–30m candles to **7 days**; uses spoofed `Mozilla/5.0` `User-Agent` instead of `TraderBot/<name-version>`. |
| `BUG-02` | Financial Math | **P1 - High** | `pnl.py:601-613, 641` | (a) Calendar resets `avg_cost = 0` *before* computing `pnl_pct` (full-exit spot sells always show `0.0%`); (b) Futures volume computes `entry_price * collateral` instead of `collateral * leverage`. |
| `BUG-03` | Cost Basis / Date Filter | **P1 - High** | `server.py:160-161`, `pnl.py:52-56, 67-73` | (a) Date-filtering trades *before* WAC breaks `avg_cost` and turns valid sells into unmatched sells; (b) Spot portfolio ignores `-RLS`/`-IRT` markets; (c) `fetch_wallets` omits `type=margin`. |
| `BUG-04` | Timezone & Boundaries | **P1 - High** | `server.py:232, 251, 462`, `pnl.py:52-56`, `db.py:394-399, 535-542` | Mixed naive local OS time (`datetime.now()`, `fromtimestamp()`) and UTC string date comparisons vs IRST (`UTC+03:30`), violating ADR-010 around midnight Tehran time. |
| `API-05` | Auth & Error Handling | **P1 - High** | `nobitex_client.py:88-95, 219-288`, `server.py:154, 456, 829-838` | Public `/market/stats` fails if `.env` keys are missing; no fail-fast on `401`/`403` (risking 30-min IP ban after 100 failed auths); `cancel_order` maps all business errors to HTTP 500 instead of parsing `code`. |
| `DB-01` | SQLite Concurrency | **P1 - High** | `db.py:24-31, 183-770`, `server.py:65-67` | `init_db()` runs DDL on every single query; WAL mode and busy timeout are not enabled; unbounded `_candle_cache` in memory; unclosed `requests.Session` per request. |
| `OPT-01` | Dynamic Metadata | **P2 - Medium** | `server.py:95-113`, `pnl.py:403, 456` | Replace hardcoded `leverage_map` and hardcoded margin fee/renewal rates with live `GET /margin/markets/list` and `GET /v2/options`. |
| `OPT-02` | Monetary Precision | **P2 - Medium** | `pnl.py`, `db.py`, `nobitex_client.py` | Migrate financial arithmetic from binary `float` to `decimal.Decimal` (recommended by Nobitex `general_notes`). |
| `OPT-03` | Real-Time WebSocket | **P2 - Enhancement** | `server.py`, `index.html` | Replace polling with backend-proxied Nobitex Centrifugo WebSocket (`public:market-stats-all`, `public:candle-*`, `private:orders#*`, `private:trades#*`). |
| `OPT-04` | True Equity & Cashflows | **P2 - Enhancement** | `nobitex_client.py`, `pnl.py`, `db.py` | Integrate `/users/portfolio/*`, `/users/wallets/deposits/list`, `/users/wallets/withdraws/list` for deposit-adjusted equity curves (TWR) and external-inflow cost basis tracking. |
| `OPT-05` | Margin & Bulk Actions | **P3 - Enhancement** | `nobitex_client.py`, `server.py`, `index.html` | Add `POST /positions/{id}/close`, `POST /positions/{id}/edit-collateral` (from Liquidation Radar), `POST /market/orders/cancel-old`, `GET /positions/{id}/status`, and `GET /users/profile`. |

---

## 2. Detailed Findings: API Compliance & Critical Bugs

### `API-01` [P0]: Missing Rate-Limit (`429` / `backOff`) Handling & Aggressive Order Pagination
- **Files:** `src/nob/nobitex_client.py` (lines 97–288), `src/nob/server.py` (lines 215–239, 570–669)
- **Nobitex Docs Spec (`general_notes` & endpoint pages):**
  - Rate limits are enforced independently per endpoint:
    - `GET /market/trades/list`: **30 req/min**
    - `GET /market/orders/list`: **30 req/min**
    - `GET /positions/list`: **30 req/10 min** (very strict!)
    - `GET /positions/{id}/status`: **100 req/10 min**
    - `GET /market/stats`: **20 req/min**
    - `GET /market/udf/history`: **60 req/min**
    - `POST /users/wallets/list`: **20 req/min**
    - `POST /market/orders/update-status`: **90 req/min**
  - On `HTTP 429`, response JSON contains `{"status": "failed", "code": "TooManyRequests", "backOff": <seconds>, "limit": <int>}`.
  - Repeatedly ignoring `429` causes a **2-minute global token ban** across all endpoints.
  - Sending `>100` invalid/expired auth requests in 30 minutes causes a **30-minute IP ban**.
- **Current Problem:**
  1. `fetch_all_orders()` sleeps only `0.1s` between pages (line 266), which fires up to 10 req/s against a 30 req/min endpoint, then silently `break`s on any non-200 status (line 258) without raising or retrying.
  2. No centralized HTTP wrapper handles `429` + `backOff` sleep + retry or transient `5xx`/network retries.
  3. `401`/`403` errors are not guarded by a circuit breaker.
- **Required Fix:**
  1. Add a centralized `_request(method, path, params=None, body_dict=None, auth=True, max_retries=3)` method in `NobitexClient`:
     - Build canonical `full_path` (including sorted/encoded query string) and exact compact JSON `raw_body = json.dumps(body_dict, separators=(",", ":"))` before signing.
     - Always send `User-Agent: TraderBot/NobAPI-2.1.0` and `Accept: application/json`.
     - If `response.status_code == 429`: parse `backOff` (default `5`s, clamp max `60`s), sleep `backOff + 0.5`s, and retry up to `max_retries`.
     - If `response.status_code in (401, 403)`: **never retry**; raise a typed `NobitexAuthError(status_code, code, message)`.
     - If `response.status_code >= 500` or `requests.RequestException`: exponential backoff (`1s`, `2s`, `4s`).
  2. Add a per-endpoint token-bucket / minimum-interval throttle in `NobitexClient` (e.g., `2.1s` for 30 req/min endpoints like `/market/orders/list` and `/market/trades/list`, `3.1s` for 20 req/min endpoints).
  3. Update `fetch_all_orders()` to use `_request()` and remove the silent `if response.status_code != 200: break`.

---

### `API-02` [P0]: Unpaginated `fetch_positions()` & Stale `Open` Positions in SQLite
- **Files:** `src/nob/nobitex_client.py` (lines 219–238), `src/nob/server.py` (lines 192–195, 424–426), `src/nob/db.py` (lines 560–658)
- **Nobitex Docs Spec (`margin_trade/مشاهده-لیست-موقعیت-ها`):**
  - `GET /positions/list` is paginated (default `pageSize=50`, max `100`) and returns `hasNext` (or `len(positions) == pageSize`).
  - Rate limit: **30 requests per 10 minutes**.
  - Liquidated/expired positions can briefly stay in `status=active` until system settlement completes.
  - Endpoint documentation notes `TRADE` permission on API Key (whereas `api-key-guide` lists it under `READ`).
- **Current Problem:**
  1. `fetch_positions()` only fetches `page=1` (`page_size=100`). Any trader with >100 closed positions loses older history.
  2. When a previously `Open` position closes, if `past` sync fails or doesn't reach that position, the old row remains `status = 'Open'` in SQLite indefinitely.
- **Required Fix:**
  1. Add `fetch_all_positions(status, page_size=100, max_pages=10)` in `NobitexClient` that loops pages while `data.get("hasNext")` (or `len(items) == page_size`), respecting the 30 req / 10 min limit. For `status="past"`, stop early once all IDs on a page already exist in SQLite as non-`Open` (incremental stop).
  2. Reconcile active positions on sync: after a successful `fetch_all_positions("active")`, any DB row currently marked `status = 'Open'` whose ID is NOT in the newly returned active IDs should be refreshed via `fetch_position_status(pos_id)` (`GET /positions/{id}/status`, 100 req/10 min) or reconciled from `past`.
  3. Surface `403` permission errors clearly in `sync_status` so users know if their API key needs additional permissions.

---

### `API-03` [P0]: Missing Incremental Sync (`fromId`) & `page`+`fromId` Parameter Conflict
- **Files:** `src/nob/nobitex_client.py` (lines 97–185, 240–267), `src/nob/server.py` (lines 413–433), `src/nob/db.py`
- **Nobitex Docs Spec:**
  - `GET /market/trades/list`: Retains only **last 180 days** of spot trades. Supports `fromId` (returns trades with `id >= fromId`), `page` (max `100`), `pageSize` (max `500`).
  - `GET /market/orders/list`: Supports `fromId` with `pageSize` up to `1000` (single page). **Explicit constraint: Do NOT send `page` and `fromId` simultaneously.**
- **Current Problem:**
  1. `fetch_trades_page()` always puts `"page": page` into `params` even when `from_id` is passed.
  2. `perform_sync()` re-downloads full history on every sync instead of passing `fromId = max_local_id + 1`.
  3. `fetch_all_orders()` hardcodes `max_pages=20` (max 2,000 orders), so older orders are dropped and their trades default to `trade_type = 'Spot'` in `upsert_trades()`.
  4. `GET /market/trades/list` only covers 180 days, so `upsert_trades()` must remain strictly additive (never delete historical rows).
- **Required Fix:**
  1. In `fetch_trades_page()` and `fetch_orders_page()`: omit `page` whenever `from_id is not None`.
  2. Add `get_max_trade_id()` and `get_max_order_id()` in `db.py`.
  3. In `perform_sync()`, support `incremental: bool = True` (default):
     - If local trades exist, fetch only `fromId = max_trade_id + 1` (and for orders, fetch `status="open"` + `fromId = max_order_id + 1` with `pageSize=1000`).
     - Provide an explicit `"Full Re-sync"` option for initial/recovery syncs.
  4. Note: `GET /market/trades/list` also accepts `tradeType` parameter (`spot` / `margin`) if needed for verification.

---

### `BUG-01` [P0]: Stale Open Orders in SQLite, Missing `Inactive`/`New` States, and Wrong Field in Asset Modal
- **Files:** `src/nob/server.py` (lines 711–715), `src/nob/db.py` (lines 236–247), `src/nob/static/index.html` (line 1188)
- **Current Problem:**
  1. In `server.py:713`: `if raw_orders: upsert_orders(raw_orders)`. When an order fills or is canceled outside the app (or when 0 open orders remain), SQLite still keeps old rows as `status = 'Active'`.
  2. In `db.py:240`: `get_open_orders()` filters `WHERE status = 'Active'`, ignoring `New` and `Inactive` (stop-loss orders waiting for trigger price are `Inactive` per `/market/orders/status` docs).
  3. In `index.html:1188`: Asset Details modal renders `${{ formatCurrency(ord.total) }}`, but `/api/orders/open` returns `ord.total_price` (not `ord.total`).
- **Required Fix:**
  1. Add `sync_open_orders_snapshot(live_open_orders)` in `db.py`: upsert all `live_open_orders`, and mark any previously `Active`/`New`/`Inactive` DB orders whose IDs are no longer in `live_open_orders` as `Closed_Or_Stale` (or `Done`/`Canceled` on next full sync).
  2. Update `get_open_orders()` to `WHERE status IN ('Active', 'New', 'Inactive')`.
  3. Fix `index.html:1188` to use `ord.total_price` (and `formatTradeTotal(ord.total_price, ord.market)` for IRT/USDT consistency).

---

### `API-04` [P1]: `/market/udf/history` 500-Candle Pagination Cap, 7-Day Intraday Limit & Wrong `User-Agent`
- **Files:** `src/nob/server.py` (lines 215–239, 570–669)
- **Nobitex Docs Spec (`market_data/دریافت-داده-های-ohlc` & Changelog Mehr 1405):**
  - Maximum **500 candles per request**. Larger ranges MUST paginate with `&page=1,2,...`.
  - Resolutions `1`, `5`, `15`, `30` (minutes) are **only available for the last 7 days**.
  - Rate limit: **60 req/min**.
  - `User-Agent` should follow `TraderBot/<name-and-version>` (not spoofed `Mozilla/5.0`).
- **Current Problem:**
  1. `api_get_candles` requests 5 years of daily candles (~1,825 bars) for `1W`, 60 days of `1H` (~1,440 bars), and 14 days of `15m`/`30m` (~1,344 / ~672 bars) in **one unpaginated call**, silently truncating history to 500 bars.
  2. Uses `urllib.request` directly with `User-Agent: Mozilla/5.0` instead of `NobitexClient`.
  3. `_candle_cache` is an unbounded dict with no max size or TTL cleanup.
- **Required Fix:**
  1. Move UDF fetching into `NobitexClient.fetch_udf_history(symbol, resolution, from_ts, to_ts)` using the shared session, `TraderBot/NobAPI-2.1.0` `User-Agent`, and `429` handling.
  2. Implement `page` pagination loop (or chunked `[from, to]` windows of `<= 500` bars) when requested range exceeds 500 candles, merging and deduplicating by timestamp `t`.
  3. Clamp default `from_ts` for `1`, `5`, `15`, `30` resolutions to `to_ts - 7 * 86400` so TradingView doesn't repeatedly request unavailable intraday ranges.
  4. Replace `_candle_cache` dict with a bounded TTL cache (e.g., max 256 entries, evicting expired/oldest keys).

---

### `BUG-02` [P1]: Financial Calculation Bugs in `pnl.py`
- **Files:** `src/nob/pnl.py` (lines 601–613, 641)
- **Current Problems:**
  1. **Zeroed `pnl_pct` on full spot exits (`pnl.py:601-613`):**
     ```python
     current_qty -= matched_qty
     if current_qty <= 1e-9:
         current_qty = 0.0
         avg_cost = 0.0  # <--- BUG: wiped before pnl_pct calculation below!
     ...
     "pnl_pct": round(((price - avg_cost) / avg_cost * 100.0), 1) if avg_cost > 0 else 0.0,
     ```
     Every time a user sells 100% of a coin, `pnl_pct` in the calendar inspector is recorded as `0.0%`.
  2. **Incorrect futures position volume (`pnl.py:641`):**
     ```python
     pos_vol = round(entry_price * collateral, 2) if dst == "USDT" else 0.0
     ```
     `collateral` is already in USDT (quote currency), NOT base coin quantity. Multiplying by `entry_price` inflates volume by e.g. `90,000x` for BTC!
- **Required Fix:**
  1. Save `cost_basis_before_reset = avg_cost` before resetting `avg_cost = 0.0`, and compute `pnl_pct` using `cost_basis_before_reset`.
  2. Change `pos_vol` to `round(collateral * float(pos.get("leverage", 1.0) or 1.0), 2)` for USDT positions (and convert RLS collateral * leverage via `usdt-rls` rate if desired).

---

### `BUG-03` [P1]: Spot Portfolio Date-Filter WAC Distortion, Missing `-RLS` Markets, and Missing Margin Wallets
- **Files:** `src/nob/server.py` (lines 160–161), `src/nob/pnl.py` (lines 52–73), `src/nob/nobitex_client.py` (lines 187–202)
- **Current Problems:**
  1. `api_get_portfolio` filters trades by `from_date` in SQL *before* passing them to `calculate_portfolio()`. Any asset bought before `from_date` and held/sold after `from_date` loses its `avg_cost` and turns sells into unmatched sells (0 realized PnL).
  2. `calculate_portfolio()` only processes `quote == "USDT"` markets (line 69), completely ignoring crypto bought/sold in `-RLS` (Toman/Rial) markets.
  3. `POST /users/wallets/list` accepts `type` (`spot` vs `margin`). `fetch_wallets()` sends an empty body, so margin wallet cash balances are never synced.
- **Required Fix:**
  1. Always pass **all chronological spot trades** into `calculate_portfolio()` to compute accurate `current_qty` and `avg_cost`; apply `[from_date, to_date]` only when accumulating `realized_pnl`, `fees_paid_usdt`, `total_bought_*`, `total_sold_*`, and `trades_timeline` (matching the pattern already used in `calculate_daily_pnl_calendar`).
  2. Support `-RLS` / `-IRT` spot trades by converting Rial prices/totals/fees to USDT using historical/latest `usdt-rls` rate (or tracking dual-quote cost basis).
  3. Update `fetch_wallets(wallet_type="spot")` to support `type="margin"` as well (sending `{"type": wallet_type}` in POST body) and store `wallet_type` in `wallet_snapshots` so total account equity accurately reflects both Spot and Margin wallets.

---

### `BUG-04` [P1]: Timezone & Date-Boundary Violations Against ADR-010
- **Files:** `src/nob/server.py` (lines 12, 232, 251, 462, 813), `src/nob/pnl.py` (lines 6, 52–56), `src/nob/db.py` (lines 394–399, 535–542)
- **Current Problems:**
  1. `sync_btc_history_if_needed()` (`server.py:232`) uses `datetime.fromtimestamp(ts)` (OS local timezone) and `api_get_equity_curve` (`server.py:251`) uses `datetime.now().strftime("%Y-%m-%d")` (OS local timezone).
  2. `db.py` and `pnl.py:calculate_portfolio` filter `from_date`/`to_date` against raw UTC ISO timestamps (`timestamp >= '2026-11-01'`), whereas Iran midnight is `20:30:00 UTC` of the previous day — creating a 3.5-hour mismatch between Portfolio/Trades and the PnL Calendar.
- **Required Fix:**
  1. Define a single constant `IRST = timezone(timedelta(hours=3, minutes=30))` in a shared helper (e.g., `src/nob/time_utils.py`) and use it everywhere instead of `ZoneInfo("Asia/Tehran")` or naive `datetime.now()`.
  2. Convert `from_date` (`YYYY-MM-DD 00:00:00+03:30` → UTC ISO) and `to_date` (`YYYY-MM-DD 23:59:59.999999+03:30` → UTC ISO) before SQL `WHERE timestamp >= ? AND timestamp <= ?` comparisons.

---

### `API-05` [P1]: Public Endpoint Coupled to Credentials & Poor Error Code Mapping
- **Files:** `src/nob/nobitex_client.py` (lines 88–95, 269–288), `src/nob/server.py` (lines 154, 456, 829–838)
- **Current Problems:**
  1. `NobitexClient()` raises `ValueError` in `__init__` if `API`/`SECRET` are missing, breaking public endpoints (`fetch_market_stats`, `fetch_udf_history`, `fetch_options`) when running without keys.
  2. `api_cancel_order` returns `HTTP 500` for every Nobitex error (including `403` missing `TRADE` permission or `OrderNotFound` / already filled).
- **Required Fix:**
  1. Make `api_key` and `secret` optional in `NobitexClient.__init__(require_auth=False)` and only validate credentials inside `_request(..., auth=True)`.
  2. Parse Nobitex's `code` field (per `general_notes`: map UI messages from `code`, not `message`) and return appropriate `400`/`401`/`403`/`422`/`429` HTTP statuses from FastAPI endpoints.

---

### `DB-01` [P1]: SQLite Concurrency, Repeated `init_db()`, Unbounded Cache & Session Leaks
- **Files:** `src/nob/db.py` (lines 24–31, all query functions), `src/nob/server.py` (lines 65–67, 567, 709, 833)
- **Current Problems:**
  1. Every function in `db.py` calls `init_db(db_path)` (executing 10+ `CREATE TABLE`/`CREATE INDEX`/`ALTER TABLE` statements on every read).
  2. SQLite connection uses default rollback journal mode and default 5s timeout — risking `database is locked` during background `perform_sync()`.
  3. FastAPI uses deprecated `@app.on_event("startup")`.
  4. Every request creates a brand-new `NobitexClient()` with a new `requests.Session()` that is never closed.
- **Required Fix:**
  1. Enable `PRAGMA journal_mode=WAL;`, `PRAGMA busy_timeout=30000;`, `PRAGMA synchronous=NORMAL;` in `get_db_connection()`.
  2. Guard `init_db()` with a module-level `_initialized_dbs: set[Path]` cache (or call only once in FastAPI `lifespan`).
  3. Use a shared/singleton `NobitexClient` instance (with thread-safe rate limiting) initialized in FastAPI `lifespan` and closed cleanly on shutdown.
  4. Switch `upsert_trades` from `INSERT OR REPLACE` to `INSERT INTO ... ON CONFLICT(id) DO UPDATE SET ...` so `created_at` is preserved.

---

## 3. Better Approaches Using Official Nobitex APIs (Architecture & Feature Upgrades)

### `OPT-01` [P2]: Replace Hardcoded Leverage & Fee Rates with Live Nobitex Endpoints
- **Currently Hardcoded:**
  - `server.py:95-113`: Hardcoded `leverage_map` (`BTC: 10`, `SOL: 5`, etc.).
  - `pnl.py:403, 456`: Hardcoded `rate = 0.00125 if dst == "rls" else 0.0008` and `0.00033333` per 8h extension fee.
- **Better Approach (Official Endpoints):**
  - **`GET /margin/markets/list`** (30 req/min; works unauthenticated, and returns **user-personalized** limits/rates when authenticated):
    - Provides live `maxLeverage`, `sellPositionFeeRate`, `buyPositionFeeRate`, and tiered daily renewal rates per market.
    - *Note from docs:* `sellPositionFeeRate` / `buyPositionFeeRate` may be absent/null in some responses — keep fallback defaults when missing.
  - **`GET /v2/options`** (public, no auth):
    - Provides all active coins/markets, price & amount decimal precisions, and minimum order sizes — ideal for `/api/markets`, `/api/chart/symbols`, TradingView `pricescale`, and Position Size Calculator rounding.
  - Cache both responses in SQLite (`metadata` or dedicated table) with a 6–24h TTL and refresh during `/api/sync`.

---

### `OPT-02` [P2]: Monetary Precision with `Decimal`
- **Nobitex Docs (`general_notes`):** Explicitly recommends string/exact decimal handling over `float` for monetary fields.
- **Better Approach:** Use `decimal.Decimal(str(val))` inside `pnl.py` (especially WAC `avg_cost`, `net_amount`, and fee deductions for micro-priced tokens like `SHIB`/`PEPE`), rounding only at the API JSON serialization boundary.

---

### `OPT-03` [P2]: Real-Time Updates via Backend-Proxied Nobitex WebSocket (Centrifugo)
- **Current Approach:** Browser polls `POST /api/prices/refresh` periodically and requires manual Sync for new trades/orders.
- **Better Approach (Official Docs: `wss://ws.nobitex.ir/connection/websocket`):**
  - **Why proxy through FastAPI backend:** Keeps API keys/tokens strictly server-side, avoids browser `Origin`/`Sec-Fetch-Site` restrictions (enforced since Mordad 1405 / Aug 2026), and honors ADR-012 (zero external browser connections).
  - **Public Channels (no token):**
    - `public:market-stats-all` → Live prices + **`mark` price** (feeds Liquidation Risk Radar in real time without burning `GET /positions/list`'s 30 req/10 min limit!).
    - `public:candle-{SYMBOL}-{RESOLUTION}` → Live OHLC candle stream (~10s updates) to implement TradingView `subscribeBars()` cleanly.
  - **Private Channels (`READ` API key):**
    1. Call `GET /users/profile` once to read static `websocketAuthParam`.
    2. Call `GET /auth/ws/token/` to obtain a JWT (`1200s` TTL; auto-refresh before expiry).
    3. Subscribe to `private:orders#{websocketAuthParam}` and `private:trades#{websocketAuthParam}` (note: use `time` ms field in private trades, as `timestamp` is deprecated).
    4. Push incremental updates to SQLite and broadcast over local `ws://127.0.0.1:8080/ws` to Vue 3.
  - **Protocol details:** Respond to empty server ping `{}` with `{}` within 20s (or use a Centrifugo Python client). Only enable `{ delta: 'fossil' }` if using an official Centrifugo SDK that supports Fossil delta decoding.

---

### `OPT-04` [P2]: True Historical Equity Curve & Cashflow Reconciliation
- **Current Problem:** `calculate_equity_curve()` estimates `initial_equity = max(current_portfolio_value - total_period_pnl, 100.0)` and ignores deposits/withdrawals and daily mark-to-market changes of held spot assets.
- **Better Approach (Official Endpoints, `READ` permission):**
  - `POST /users/portfolio/last-week-daily-profit` (supports `monthly=true` for 30 days; 10 req/3 min)
  - `POST /users/portfolio/last-week-daily-total-profit` (supports `monthly=true`; 10 req/3 min)
  - `POST /users/portfolio/last-month-total-profit` (10 req/3 min)
  - `/users/portfolio/daily_total_balance` (listed in `api-key-guide` under `READ`)
  - `GET /users/wallets/deposits/list` & `GET /users/wallets/withdraws/list` & `GET /users/transactions-history` (60 req/hr)
- **Benefit:** Enables storing daily portfolio balance snapshots + external deposit/withdrawal cashflows in SQLite, unlocking **Time-Weighted Return (TWR)**, accurate Net Capital Inflow vs Trading PnL separation, and cost-basis tracking for externally deposited coins.

---

### `OPT-05` [P3]: Additional High-Value Features Available in Nobitex API
- **Close Margin Position (`POST /positions/{positionId}/close` — `TRADE`, 300 req/10 min):** Supports `limit`, `market`, `stop_limit`, `stop_market`, `oco` directly from the Margin tab.
- **Adjust Margin Collateral (`POST /positions/{positionId}/edit-collateral` — `TRADE`, 60 req/min):** Add collateral directly from the Liquidation Risk Radar when a position enters Danger (`<10%`) or withdraw excess collateral when in profit.
- **Bulk Cancel Old/Filtered Orders (`POST /market/orders/cancel-old` — `TRADE`, 30 req/min):** One-click "Cancel All Spot / Margin / Market Orders" button in the Open Orders tab.
- **Single Position Live Refresh (`GET /positions/{positionId}/status` — `TRADE`, 100 req/10 min):** Higher rate limit than `/positions/list` (30 req/10 min) for refreshing individual open positions.
- **Account Profile & Tier Badge (`GET /users/profile` & `POST /users/limitations` — `READ`):** Display user level, fee tier, and API key status in the header.

---

## 4. Recommended Implementation Roadmap (Ordered for AI Execution)

1. **Phase 1 — API Client & Database Hardening (`API-01`, `API-02`, `API-03`, `API-05`, `DB-01`)**
   - Refactor `NobitexClient` with centralized `_request()`, optional auth, per-endpoint rate throttling, `429` `backOff` retry, `401`/`403` fail-fast, and `TraderBot/NobAPI-2.1.0` `User-Agent`.
   - Implement incremental `fromId` sync for trades & orders (ensuring `page` and `fromId` are never sent together) and full pagination + active reconciliation for `fetch_all_positions()`.
   - Enable SQLite WAL mode, `busy_timeout=30000`, `lifespan` startup/shutdown, and singleton client.
2. **Phase 2 — Bug & Accounting Fixes (`BUG-01`, `BUG-02`, `BUG-03`, `BUG-04`)**
   - Fix `pnl.py` full-exit `pnl_pct` bug and futures `pos_vol` formula.
   - Fix `calculate_portfolio()` date-range WAC replay, add `-RLS` market support, and sync `margin` wallets.
   - Standardize `IRST = timezone(timedelta(hours=3, minutes=30))` across `server.py`, `pnl.py`, and `db.py`.
   - Fix open orders snapshot reconciliation (`Active`/`New`/`Inactive`) and `index.html:1188` (`ord.total_price`).
3. **Phase 3 — UDF Pagination & Dynamic Metadata (`API-04`, `OPT-01`, `OPT-02`)**
   - Move `/market/udf/history` into `NobitexClient` with `<= 500` bar pagination, 7-day intraday clamp, and bounded TTL cache.
   - Fetch live leverage/fee rates from `GET /margin/markets/list` and market precision from `GET /v2/options`.
   - Migrate core PnL arithmetic to `Decimal`.
4. **Phase 4 — Real-Time WebSocket & Portfolio Reconciliation (`OPT-03`, `OPT-04`, `OPT-05`)**
   - Add FastAPI background WebSocket client (`wss://ws.nobitex.ir/connection/websocket`) bridging live prices, candles, orders, and trades to the frontend over local WS.
   - Sync `/users/portfolio/*` and deposits/withdrawals for deposit-adjusted equity curves.
   - Add margin close / edit-collateral and bulk cancel-old actions (gated on `TRADE` permission).

---

## 5. Verified Nobitex API Quick Reference (`https://apidocs.nobitex.ir/`)

| Endpoint | Method | Auth / Key Perm | Rate Limit | Key Constraints & Notes |
| :--- | :--- | :--- | :--- | :--- |
| `/market/stats` | `GET` | Public | 20 / min | `srcCurrency` & `dstCurrency` optional; `global` field deprecated. |
| `/market/udf/history` | `GET` | Public | 60 / min | Max **500 candles/req** (use `page`); `1,5,15,30` min only for **last 7 days**. |
| `/v2/options` | `GET` | Public | — | All coins, networks, min orders, price/amount precision. |
| `/margin/markets/list` | `GET` | Public or Auth | 30 / min | Live `maxLeverage`, fee rates & renewal rates (personalized if authenticated). |
| `/users/profile` | `GET` | `READ` | — | Returns user profile & static `websocketAuthParam`. |
| `/users/wallets/list` | `POST` | `READ` | 20 / min | Pass `type` (`spot` / `margin`); POST recommended. |
| `/v2/wallets` | `POST` | `READ` | 15 / min | Selective wallets by currency list and type. |
| `/market/trades/list` | `GET` | `READ` | 30 / min | **Last 180 days** only; `page` max 100, `pageSize` max 500; `fromId` (`id >=`). |
| `/market/orders/list` | `GET` | `READ` | 30 / min | Default `pageSize=100`; with `fromId` max `1000` (1 page); **never mix `page` + `fromId`**. |
| `/market/orders/status` | `POST` | `READ` | 300 / min | States: `New`, `Active`, `Inactive`, `Done`, `Canceled`. |
| `/market/orders/update-status` | `POST` | `TRADE` | 90 / min | Business errors return HTTP 200 with `status: "failed"`, `updatedStatus`, `order`. |
| `/market/orders/cancel-old` | `POST` | `TRADE` | 30 / min | Bulk cancel `Active` orders; omits non-OCO `Inactive` stop orders. |
| `/positions/list` | `GET` | `TRADE`* | **30 / 10 min** | `status=active\|past`; default `pageSize=50`; liquidated positions briefly stay in `active`. |
| `/positions/{id}/status` | `GET` | `TRADE`* | 100 / 10 min | Single position status. |
| `/positions/{id}/close` | `POST` | `TRADE` | 300 / 10 min | Supports `limit`, `market`, `stop_limit`, `stop_market`, `oco`. |
| `/positions/{id}/edit-collateral` | `POST` | `TRADE` | 60 / min | Increase collateral (improve liq price) or decrease excess collateral. |
| `/users/portfolio/last-week-daily-profit` | `POST` | `READ` | 10 / 3 min | Pass `monthly=true` for 30 days. |
| `/users/portfolio/last-week-daily-total-profit` | `POST` | `READ` | 10 / 3 min | Pass `monthly=true` for 30 days. |
| `/users/portfolio/last-month-total-profit` | `POST` | `READ` | 10 / 3 min | Monthly total portfolio PnL. |
| `/users/transactions-history` | `GET` | `READ` | 60 / hour | Supports `download=true` (CSV) and bounded date range. |
| `/auth/ws/token/` | `GET` | `READ` | — | Returns Centrifugo JWT (`1200s` TTL) for private WS channels. |
| `wss://ws.nobitex.ir/connection/websocket` | `WS` | Public / JWT | 100 conns, 450 ch | Ping/pong `{}` within 20s; `public:market-stats-all`, `public:candle-*`, `private:orders#*`, `private:trades#*`. |

*\*Note: Nobitex's `api-key-guide` lists `/positions/list` under `READ`, while the endpoint reference page specifies `TRADE`. Handle `403` gracefully and inform the user.*
