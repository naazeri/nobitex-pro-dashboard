<div align="center">

# Nobitex Portfolio & Trades Dashboard 📊

**A modern, private, and offline-first analytics web dashboard for spot and leveraged (futures/margin) trading on Nobitex.**

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Offline First](https://img.shields.io/badge/architecture-offline--first-success.svg)](#privacy--security)
[![Architecture Log](https://img.shields.io/badge/architecture-ADR%20Log-blueviolet.svg)](DECISIONS.md)

[🇮🇷 **مطالعه به زبان فارسی (Persian README)**](README.fa.md) · [Features](#-key-features) · [Quickstart](#-quickstart) · [Decisions Log](DECISIONS.md) · [Security](#-privacy--security)

</div>

---

## 📖 Overview

**Nobitex Portfolio & Trades Dashboard** is a standalone, local-first web application designed for active traders on the Nobitex cryptocurrency exchange. It turns raw order and trade history into actionable intelligence through interactive risk management tools, visual PnL calendars, an equity curve, position sizing calculators, professional TradingView charting, and a discipline-focused trading journal.

Designed from the ground up to be **100% offline-ready and private**: your trading data and API secrets never leave your local machine.

---

## 🌟 Key Features

### 1. 📊 Portfolio & Asset Overview
- **Real-Time Valuation:** Aggregates spot balances and active margin collateral with live Nobitex market prices.
- **True Net PnL:** All profit/loss numbers strictly deduct buy/sell transaction fees, margin opening/closing/renewal fees, and Nobitex pool profit share (سهم استخر مشارکت).
- **Dual Currency:** Displays values seamlessly in Tether (USD) and Iranian Toman (IRT).
- **Spot Asset Details Modal:** Click any spot asset row to inspect an in-depth breakdown modal with Weighted Average Cost (WAC), total bought/sold volume, realized/unrealized PnL, embedded open orders, and a complete 7-column transaction ledger.

### 2. 📉 Offline TradingView Advanced Charting Engine
- **Professional Analytics:** Powered by a 100% offline-bundled **TradingView Advanced Charting Library (v28)** with 100+ technical indicators, drawing tools, and customizable styles.
- **Multi-Timeframe Resolutions:** Seamlessly switch between `15m`, `30m`, `1h`, `4h`, `1D`, and `1W` candles with server-side caching and aggregation.
- **URL & State Synchronization:** Chart symbols and timeframes are synchronized bi-directionally with the browser address bar (`?tab=chart&symbol=USOONUSDT&tf=1D`) with isolated `localStorage` to avoid stale symbol overrides.
- **Dual Calendar on Timeline:** Displays Jalali (Solar Hijri) dates on the chart timeline in Persian mode and Gregorian in English mode.

### 3. 📅 Daily PnL Calendar
- **Interactive Monthly Grid:** Color-coded daily profit/loss cells (Green for profit, Red for loss, Gray for inactive days).
- **Integrated Futures & Spot:** Incorporates both USDT and IRT margin closed positions and spot executions into daily performance stats.
- **Dual Calendar Engine:** Full support for **Jalali (Solar Hijri)** in Persian mode and **Gregorian** in English mode.
- **Day Inspector:** Click any calendar day to inspect every spot execution and closed futures position settled on that date.

### 4. 📈 Equity Curve & Cumulative PnL
- **Continuous Historical Timeline:** Visualizes total account portfolio growth and cumulative net returns over time.
- **Bitcoin Benchmark (Alpha Comparison):** Overlay Bitcoin's return trajectory on the same chart to compare your performance against the market benchmark.
- **Risk Metrics:** Automatically calculates Maximum Drawdown (MDD), Peak Account Equity, and Alpha vs BTC.
- **Dynamic Date Filtering:** Presets for `All Time`, `30D`, `90D`, `YTD`, and `Custom` date ranges, fully persistent across page refreshes via URL parameters.

### 5. ⚡ Liquidation Risk Radar
- **Live Safety Margin Meter:** Continuous monitoring of liquidation proximity for every active leveraged position:
  $$\text{Liquidation Distance \%} = \frac{|\text{Mark Price} - \text{Liquidation Price}|}{\text{Mark Price}} \times 100$$
- **Color-Coded Risk Tiers:** 
  - 🟢 **Safe:** Distance > 20%
  - 🟡 **Caution:** Distance between 10% and 20%
  - 🔴 **High Danger:** Distance < 10% (with pulsing visual alert)
- **Offline Audio Alert:** Web Audio API sound chime when risk levels enter critical thresholds (toggleable, zero external audio assets).

### 6. 📋 Nobitex Open Orders Management
- **Live Open Orders Tab:** View and filter all active Spot and Margin orders by market and side (Buy/Sell).
- **In-App Order Cancellation:** Cancel open limit or stop orders directly from the dashboard UI with optimistic updates (requires Trading API permission).
- **Embedded Asset Orders:** View active open orders for any specific asset inside its Asset Details modal.

### 7. 🧮 Position Size & Risk Calculator
- **Capital Protection Sizing:** Mathematically determines exact position size based on risk tolerance ($ or % of equity):
  $$\text{Position Size} = \frac{\text{Risk Amount (\text{\$})}}{|\text{Entry Price} - \text{Stop Loss}|}$$
- **Recommended Leverage Guidance:** Suggests optimal leverage that keeps the liquidation price safely beyond your stop-loss.
- **Critical Collision Alert:** High-visibility alert if chosen leverage causes liquidation to hit *before* your stop-loss.
- **1-Click Trade Plan Copy:** Formats and copies full order specifications to clipboard for execution or sharing.

### 8. 📝 Trading Journal & Strategy Playbook
- **Technical Strategy Tagging:** Label trades with presets (*Breakout, Trend, Pullback, Support Bounce, Scalp, DCA*) or custom tags.
- **Psychology & Discipline Tracking:** Log mental state (*Disciplined, FOMO, Revenge Trade, News, Early Exit*).
- **Net Amount Tracking:** Dedicated `Net Amount` column (`Amount - Fee` for buys, dash for sells) showing exact net coins acquired.
- **Strategy Analytics Bar:** Filter trade history by any tag with one click to discover which setups generate real profit.

### 9. 🖥️ Widescreen & High-Resolution Layout (1920px)
- **Widescreen Optimized:** Expanded container max-width to `1920px` (`max-w-[1920px]`), utilizing large 2K, 4K, and Ultrawide monitors without cramped columns.
- **Roomy Modals:** Expanded modal widths (`max-w-5xl` = 1024px) for comfortable multi-column ledger viewing.
- **BiDi Numerical Isolation:** CSS `.bidi-num` and Canvas 2D LTR enforcement ensure negative amounts, percentages, and currencies are never inverted in RTL mode.

---

## 🚀 Quickstart

### Prerequisites
- Python 3.12 or higher
- [uv](https://github.com/astral-sh/uv) (recommended) or standard `pip`

### 1. Clone & Install

```bash
git clone https://github.com/<your-username>/nob-api.git
cd nob-api
```

Install dependencies using `uv`:
```bash
uv sync
```
*(Or with standard pip: `pip install -e .`)*

### 2. Configure Credentials

Copy the template environment file:
```bash
cp .env.example .env
```

Edit `.env` with your Nobitex API keys:
```env
# Nobitex API Credentials (Read-Only recommended)
API=your_nobitex_api_key_here
SECRET=your_nobitex_secret_key_here
```

> 🔒 **Security Best Practice:** Generate a **Read-Only** API key from your Nobitex account settings for viewing portfolio data. If you wish to use the *Cancel Order* feature, grant the key Trade permission.

### 3. Run the Dashboard

Start the application on port **8080**:
```bash
uv run uvicorn nob.server:app --host 127.0.0.1 --port 8080 --reload --app-dir src
```

Or using the installed console script:
```bash
uv run nob-web
```

Open your browser and navigate to:
```
http://127.0.0.1:8080
```

---

## 🏛️ Architectural & Product Decisions

Every significant technical decision (such as True Net PnL math, TradingView offline integration, LocalStorage isolation, 1920px widescreen layout, and date filter design) is documented with its rationale and *Do Not Revert* guidelines in:

👉 **[DECISIONS.md](DECISIONS.md)**

---

## 🔒 Privacy & Security

| Feature | Implementation |
| :--- | :--- |
| **Data Storage** | All trade records, cached market prices, and personal journal notes are stored strictly in local SQLite (`data/portfolio.db`). |
| **Git Protection** | Pre-configured [.gitignore](.gitignore) prevents `.env`, `portfolio.db`, logs, or private credentials from ever being staged or committed. |
| **Zero External CDNs** | Zero external analytics, tracking scripts, or CDN dependencies. Frontend assets (Vue 3, Chart.js, Tailwind CSS, TradingView Advanced v28) and fonts are bundled 100% locally. |
| **API Safety** | Operates safely with read-only credentials. Never initiates withdrawals or unsanctioned orders. |

---

## 📂 Project Structure

```text
nob-api/
├── data/                    # Local SQLite databases (git-ignored)
│   └── portfolio.db
├── graphify-out/            # Knowledge graph & project memory (Graphify)
│   ├── graph.json
│   ├── graph.html
│   └── GRAPH_REPORT.md
├── src/
│   └── nob/
│       ├── db.py            # SQLite schema, migrations, Net Amount & PnL queries
│       ├── nobitex_client.py# Nobitex REST API client with retry & rate limiting
│       ├── pnl.py           # Portfolio valuation, calendar, and equity curve engine
│       ├── server.py        # FastAPI server, orders API, and TradingView datafeed
│       ├── export_csv.py    # CSV export utility with UTF-8 BOM
│       └── static/          # Single-Page Application (HTML5, Tailwind, Vue 3, i18n, TV)
│           ├── i18n.js      # English & Persian translations
│           └── vendor/      # 100% local offline libraries (TradingView v28, Vue, Chart.js)
├── DECISIONS.md             # Architectural Decision Records (ADR log)
├── .env.example             # Safe template for credentials
├── .gitignore               # Strict exclusion rules for open-source safety
├── LICENSE                  # MIT License
├── pyproject.toml           # Package metadata and dependencies
└── README.md                # Project documentation
```

---

## 🤝 Contributing

Contributions, issues, and feature requests are welcome!
Feel free to check [issues page](https://github.com/<your-username>/nob-api/issues).

---

## 📄 License

This project is open-source and licensed under the [MIT License](LICENSE).
