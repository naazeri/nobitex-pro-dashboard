<div align="center">

# Nobitex Portfolio & Trades Dashboard 📊

**A modern, private, and offline-first analytics web dashboard for spot and leveraged (futures/margin) trading on Nobitex.**

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Offline First](https://img.shields.io/badge/architecture-offline--first-success.svg)](#privacy--security)

[🇮🇷 **مطالعه به زبان فارسی (Persian README)**](README.fa.md) · [Features](#-key-features) · [Quickstart](#-quickstart) · [Security](#-privacy--security)

</div>

---

## 📖 Overview

**Nobitex Portfolio & Trades Dashboard** is a standalone web application designed for active traders on the Nobitex cryptocurrency exchange. It turns raw order and trade history into actionable intelligence through interactive risk management tools, visual PnL calendars, an equity curve, position sizing calculators, and a discipline-focused trading journal.

Designed from the ground up to be **100% offline-ready and private**: your trading data and API secrets never leave your local machine.

---

## 🌟 Key Features

### 1. 📊 Portfolio & Asset Overview
- **Real-Time Valuation:** Aggregates spot balances and active margin collateral with live Nobitex market prices.
- **Futures Performance Metrics:** Total realized & unrealized PnL (in USD and IRT), Win Rate, win/loss ratio, and best trade showcases.
- **Dual Currency:** Displays values seamlessly in Tether (USD) and Iranian Toman (IRT).

### 2. 📅 Daily PnL Calendar
- **Interactive Monthly Grid:** Color-coded daily profit/loss cells (Green for profit, Red for loss, Gray for inactive days).
- **Dual Calendar Engine:** Full support for **Jalali (Solar Hijri)** in Persian mode and **Gregorian** in English mode.
- **Day Inspector:** Click any calendar day to inspect every spot execution and closed futures position settled on that date.
- **Monthly Summary:** Total net PnL, best trading day, worst drawdown day, and daily win rate.

### 3. 📈 Equity Curve & Cumulative PnL
- **Continuous Historical Timeline:** Visualizes total account portfolio growth and cumulative net returns over time.
- **Bitcoin Benchmark (Alpha Comparison):** Overlay Bitcoin's return trajectory on the same chart to compare your performance against the market benchmark.
- **Risk Metrics:** Automatically calculates Maximum Drawdown (MDD), Peak Account Equity, and Alpha vs BTC.
- **Flexible Ranges:** 7 Days, 30 Days, 90 Days, and All-Time views.

### 4. ⚡ Liquidation Risk Radar
- **Live Safety Margin Meter:** Continuous monitoring of liquidation proximity for every active leveraged position:
  $$\text{Liquidation Distance \%} = \frac{|\text{Mark Price} - \text{Liquidation Price}|}{\text{Mark Price}} \times 100$$
- **Color-Coded Risk Tiers:** 
  - 🟢 **Safe:** Distance > 20%
  - 🟡 **Caution:** Distance between 10% and 20%
  - 🔴 **High Danger:** Distance < 10% (with pulsing visual alert)
- **Offline Audio Alert:** Web Audio API sound chime when risk levels enter critical thresholds (toggleable, zero external audio assets).

### 5. 🧮 Position Size & Risk Calculator
- **Capital Protection Sizing:** Mathematically determines exact position size based on risk tolerance ($ or % of equity):
  $$\text{Position Size} = \frac{\text{Risk Amount (\text{\$})}}{|\text{Entry Price} - \text{Stop Loss}|}$$
- **Recommended Leverage Guidance:** Suggests optimal leverage that keeps the liquidation price safely beyond your stop-loss.
- **Critical Collision Alert:** High-visibility alert if chosen leverage causes liquidation to hit *before* your stop-loss.
- **1-Click Trade Plan Copy:** Formats and copies full order specifications to clipboard for execution or sharing.

### 6. 📝 Trading Journal & Strategy Playbook
- **Technical Strategy Tagging:** Label trades with presets (*Breakout, Trend, Pullback, Support Bounce, Scalp, DCA*) or custom tags.
- **Psychology & Discipline Tracking:** Log mental state (*Disciplined, FOMO, Revenge Trade, News, Early Exit*).
- **Execution Sentiment:** Rate trade execution quality (🟢 Disciplined, 🟡 Impulsive, 🔴 Emotional).
- **Strategy Analytics Bar:** Filter trade history by any tag with one click to discover which setups generate real profit.

### 7. 🌐 Dual Language & Instant Localization
- Complete **Persian (RTL)** and **English (LTR)** internationalization.
- One-click toggle between languages without reloading data.

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

> 🔒 **Security Best Practice:** Generate a **Read-Only** API key from your Nobitex account settings. This application only reads market data, order history, and account balances; it never places orders or initiates withdrawals.

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

## 🔒 Privacy & Security

| Feature | Implementation |
| :--- | :--- |
| **Data Storage** | All trade records, cached market prices, and personal journal notes are stored strictly in local SQLite (`data/portfolio.db`). |
| **Git Protection** | Pre-configured [.gitignore](.gitignore) prevents `.env`, `portfolio.db`, logs, or private credentials from ever being staged or committed. |
| **No External Telemetry** | Zero external analytics, tracking scripts, or CDN dependencies. Frontend assets (Vue 3, Chart.js, Tailwind CSS) are bundled locally. |
| **API Safety** | Works completely with read-only credentials. Never requests trade or withdrawal permissions. |

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
│       ├── db.py            # SQLite schema, migrations, and queries
│       ├── nobitex_client.py# Nobitex REST API client with retry logic
│       ├── pnl.py           # Portfolio valuation, calendar, and equity curve engine
│       ├── server.py        # FastAPI server and REST endpoints (port 8080)
│       ├── export_csv.py    # CSV export utility with UTF-8 BOM
│       └── static/          # Single-Page Application (HTML5, Tailwind, Vue 3, i18n)
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
