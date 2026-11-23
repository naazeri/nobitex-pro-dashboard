# Graph Report - nob-api  (2026-11-24)

## Corpus Check
- 25 files · ~67,022 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 5 file(s) not represented in the graph (top: (none) 3, .example 1, .lock 1)

## Summary
- 658 nodes · 1490 edges · 34 communities (22 shown, 12 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 66 edges (avg confidence: 0.9)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Spot WAC & Margin PnL Engine
- Architectural Decisions & Docs
- Test Fixtures & SQLite Suite
- Vue 3 Dashboard UI & Modals
- Centrifugo WebSocket Bridge
- Frontend Formatters & IRST Utils
- FastAPI Trade Action Routes
- FastAPI Server Core & Middleware
- SQLite Upsert & Snapshot Storage
- SQLite Connection & Order Queries
- SQLite Trade Filter & Journal DB
- SQLite Historical Data Queries
- Nobitex REST Client & Cashflows
- API Rate Limiter & Auth Loader
- Portfolio & Trade Query Routes
- FastAPI Security Test Suite
- Client Auth & Retry Test Suite
- Localhost Origin & WS Guard
- Ed25519 Request Signing
- Frontend Integrity Test Suite
- Nobitex API Error Hierarchy
- Market Metadata Cache & Routes
- OHLCV Candle Cache & UDF Route
- Streaming CSV Export Pipeline
- Jalali Calendar UI Module
- Dashboard KPI Stats Query
- i18n Translations & Date Format
- Frontend Fetch & Race Guard
- Journal Delete Route
- Project Root Package

## God Nodes (most connected - your core abstractions)
1. `NobitexClient` - 46 edges
2. `get_db_connection()` - 43 edges
3. `init_db()` - 43 edges
4. `Vue 3 Dashboard App` - 36 edges
5. `NobitexWSBridge` - 30 edges
6. `now_irst()` - 29 edges
7. `perform_sync()` - 25 edges
8. `calculate_portfolio()` - 18 edges
9. `calculate_daily_pnl_calendar()` - 17 edges
10. `get_nobitex_client()` - 17 edges

## Surprising Connections (you probably didn't know these)
- `test_401_403_fail_fast_zero_retries()` --uses--> `NobitexAuthError`  [INFERRED]
  tests/test_nobitex_client.py → src/nob/nobitex_client.py
- `test_429_respects_backoff_and_raises_after_retries()` --uses--> `NobitexRateLimitError`  [INFERRED]
  tests/test_nobitex_client.py → src/nob/nobitex_client.py
- `test_401_403_fail_fast_zero_retries()` --uses--> `NobitexClient`  [INFERRED]
  tests/test_nobitex_client.py → src/nob/nobitex_client.py
- `test_429_respects_backoff_and_raises_after_retries()` --uses--> `NobitexClient`  [INFERRED]
  tests/test_nobitex_client.py → src/nob/nobitex_client.py
- `test_thread_local_sessions_isolated_and_closed()` --uses--> `NobitexClient`  [INFERRED]
  tests/test_nobitex_client.py → src/nob/nobitex_client.py

## Import Cycles
- None detected.

## Communities (34 total, 12 thin omitted)

### Community 0 - "Spot WAC & Margin PnL Engine"
Cohesion: 0.09
Nodes (28): calculate_daily_pnl_calendar(), calculate_equity_curve(), calculate_portfolio(), is_in_range(), calculate_positions_summary(), _cashflow_amount_to_usdt(), _convert_quote_to_usdt(), D() (+20 more)

### Community 1 - "Architectural Decisions & Docs"
Cohesion: 0.07
Nodes (35): IRST UTC+03:30 Timezone & BiDi Numerical Isolation, Localhost Security Boundaries (CSWSH, DNS Rebinding & Trade Guardrails), DECISIONS.md: Architectural & Product Decision Log, Offline TradingView Advanced v28 & Symbol/Timeframe Integrity, Dedicated Orders Sync Cursor & Spot/Margin Self-Healing, Spot Equity Curve Isolation & Cash Completeness, SQLite Connection Lifecycle, Indexing & Streaming Export, True Net PnL Accounting Model (+27 more)

### Community 2 - "Test Fixtures & SQLite Suite"
Cohesion: 0.06
Nodes (14): isolated_db(), stub_client(), StubNobitexClient, test_atomic_reconcile_open_orders_handles_large_batches(), test_get_db_connection_always_closes_on_success_and_error(), test_get_trades_like_escaping_and_single_query_stats(), test_metadata_cache_handles_naive_and_aware_timestamps(), test_secondary_indexes_and_wal_mode_created() (+6 more)

### Community 3 - "Vue 3 Dashboard UI & Modals"
Cohesion: 0.08
Nodes (48): Backend APIs /api/chart/symbols & /api/chart/candles, Backend API /api/analytics/equity-curve, Backend API /api/export/csv, Backend API /api/journal, Backend APIs /api/orders/cancel & /api/orders/cancel-old, Backend API /api/orders/open, Backend API /api/analytics/pnl-calendar, Backend API /api/portfolio (+40 more)

### Community 4 - "Centrifugo WebSocket Bridge"
Cohesion: 0.10
Nodes (7): _safe_float(), _ms_to_utc_iso(), NobitexWSBridge, normalize_ws_candle_target(), normalize_ws_order_event(), normalize_ws_trade_event(), parse_centrifugo_frame()

### Community 5 - "Frontend Formatters & IRST Utils"
Cohesion: 0.08
Nodes (26): formatAmount(), formatDateIsoTehran(), formatNetAmount(), formatPositionCollateral(), formatPositionFee(), formatPositionPnl(), formatPositionPrice(), formatTomanLabel() (+18 more)

### Community 7 - "FastAPI Trade Action Routes"
Cohesion: 0.09
Nodes (16): api_cancel_old_orders(), api_cancel_order(), api_close_position(), api_edit_position_collateral(), api_save_chart_state(), api_save_journal(), api_sync_trades(), CancelOldOrdersRequest (+8 more)

### Community 8 - "FastAPI Server Core & Middleware"
Cohesion: 0.09
Nodes (7): get_open_orders(), upsert_btc_daily_history(), api_get_open_orders(), api_get_user_profile(), get_nobitex_client(), lifespan(), sync_btc_history_if_needed()

### Community 9 - "SQLite Upsert & Snapshot Storage"
Cohesion: 0.13
Nodes (14): _chunked(), _fetch_journal_map(), reconcile_open_orders(), set_metadata_json(), upsert_cashflows(), upsert_daily_equity_snapshots(), upsert_market_prices(), upsert_orders() (+6 more)

### Community 10 - "SQLite Connection & Order Queries"
Cohesion: 0.14
Nodes (12): delete_journal_entry(), get_active_position_ids(), get_db_connection(), get_known_closed_position_ids(), get_margin_trades(), get_max_trade_id(), get_unlinked_trade_order_ids(), init_db() (+4 more)

### Community 11 - "SQLite Trade Filter & Journal DB"
Cohesion: 0.14
Nodes (11): _build_trades_where_clause(), _escape_like(), get_all_journal_entries(), get_chart_state(), get_journal_entry(), get_journal_tags_summary(), _parse_journal_tags(), save_chart_state() (+3 more)

### Community 12 - "SQLite Historical Data Queries"
Cohesion: 0.10
Nodes (10): get_all_crypto_trades_chronological(), get_btc_daily_history(), get_daily_equity_snapshots(), get_market_prices(), get_positions(), upsert_positions(), api_get_chart_symbols(), api_get_equity_curve() (+2 more)

### Community 14 - "API Rate Limiter & Auth Loader"
Cohesion: 0.13
Nodes (5): _EndpointRateLimiter, find_env_file(), load_credentials(), load_dotenv(), _normalize_endpoint_key()

### Community 15 - "Portfolio & Trade Query Routes"
Cohesion: 0.11
Nodes (10): get_cashflows(), get_trades(), get_wallet_snapshots(), api_get_cashflows(), api_get_chart_state(), api_get_portfolio(), api_get_portfolio_charts(), api_get_trades() (+2 more)

### Community 16 - "FastAPI Security Test Suite"
Cohesion: 0.20
Nodes (8): client(), _noop_async(), test_cswsh_websocket_origin_rejected_and_localhost_allowed(), test_dns_rebinding_and_cross_origin_mutation_blocked(), test_on_demand_positions_cooldown_and_sync_lock(), test_streaming_csv_export_with_filters(), test_trade_action_guardrails_and_decimal_formatting(), test_trades_pagination_and_candles_validation()

### Community 17 - "Client Auth & Retry Test Suite"
Cohesion: 0.24
Nodes (4): test_401_403_fail_fast_zero_retries(), test_429_respects_backoff_and_raises_after_retries(), test_thread_local_sessions_isolated_and_closed(), _valid_secret_b64()

### Community 18 - "Localhost Origin & WS Guard"
Cohesion: 0.17
Nodes (5): api_ws_status(), _enforce_local_origin_on_mutations(), get_ws_bridge(), _is_allowed_local_origin(), websocket_endpoint()

### Community 19 - "Ed25519 Request Signing"
Cohesion: 0.32
Nodes (4): NobitexAuthError, parse_private_key(), sign_request(), test_parse_private_key_wraps_invalid_secret_in_auth_error()

### Community 20 - "Frontend Integrity Test Suite"
Cohesion: 0.46
Nodes (3): test_adr012_zero_external_cdns_and_zero_xss_sinks(), test_all_i18n_keys_used_in_index_exist_in_en_and_fa(), test_local_es_module_imports_resolve_and_pass_node_syntax_check()

### Community 22 - "Market Metadata Cache & Routes"
Cohesion: 0.25
Nodes (4): get_distinct_markets(), get_metadata_cache(), get_metadata_json(), api_get_markets()

### Community 24 - "OHLCV Candle Cache & UDF Route"
Cohesion: 0.40
Nodes (4): api_get_candles(), _get_cached_candles(), _set_cached_candles(), _update_sync_status()

### Community 25 - "Streaming CSV Export Pipeline"
Cohesion: 0.40
Nodes (3): iter_trades_for_export(), api_export_csv(), _csv_stream()

## Knowledge Gaps
- **24 isolated node(s):** `nob-api`, `ADR-0010`, `ADR-0012`, `ADR-0010`, `ADR-0012` (+19 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 250 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **12 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `NobitexClient` connect `Nobitex REST Client & Cashflows` to `Centrifugo WebSocket Bridge`, `Nobitex Margin & Order Client`, `FastAPI Server Core & Middleware`, `API Rate Limiter & Auth Loader`, `Client Auth & Retry Test Suite`, `Nobitex API Error Hierarchy`, `Incremental Trade Sync Client`, `Crypto Deposit Ledger Client`?**
  _High betweenness centrality (0.141) - this node is a cross-community bridge._
- **Are the 5 inferred relationships involving `NobitexClient` (e.g. with `NobitexWSBridge` and `test_401_403_fail_fast_zero_retries()`) actually correct?**
  _`NobitexClient` has 5 INFERRED edges - model-reasoned connections that need verification._
- **What connects `nob-api`, `ADR-0010`, `ADR-0012` to the rest of the system?**
  _24 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Spot WAC & Margin PnL Engine` be split into smaller, more focused modules?**
  _Cohesion score 0.09084556254367575 - nodes in this community are weakly interconnected._
- **Why does `NobitexWSBridge` connect `Centrifugo WebSocket Bridge` to `FastAPI Server Core & Middleware`, `Nobitex REST Client & Cashflows`, `Localhost Origin & WS Guard`, `Ed25519 Request Signing`, `Nobitex API Error Hierarchy`?**
  _High betweenness centrality (0.055) - this node is a cross-community bridge._
- **Should `Architectural Decisions & Docs` be split into smaller, more focused modules?**
  _Cohesion score 0.07102040816326531 - nodes in this community are weakly interconnected._
- **Should `Test Fixtures & SQLite Suite` be split into smaller, more focused modules?**
  _Cohesion score 0.06205673758865248 - nodes in this community are weakly interconnected._