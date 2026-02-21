# Portfolio Telegram Analytics

A **read-only** Python analytics tool that ingests **portfolio snapshot CSV(s)** (positions, not trades), pulls **historical daily prices** from :contentReference[oaicite:0]{index=0} (with free fallback to :contentReference[oaicite:1]{index=1}), computes descriptive metrics, analyzes **TSLA concentration**, runs a **one-time static rebalance simulation**, and delivers results via a :contentReference[oaicite:2]{index=2} bot + weekly summary.

✅ No trading, no forecasting, no brokerage APIs, no ML/AI models  
✅ Descriptive analytics only + a simple static rebalance simulation  
✅ Modular, logged, and safe-by-default

---

## What it does (high level)

1. **Ingest portfolio CSV snapshots** (positions as-of a date)
2. **Fetch last ~3 months daily prices** (≈63 trading days)
3. Compute metrics for:
   - Portfolio
   - Benchmarks: **SPY** (S&P 500 proxy) and **IWM** (Russell 2000 proxy)
4. **TSLA concentration analysis**
5. **Static rebalance simulation**
   - Reduce TSLA share count by **25%**
   - Reallocate freed cash **pro-rata** across remaining holdings
6. Outputs:
   - Short text report (Telegram)
   - Charts (cumulative return & drawdown)
   - Weekly Telegram summary message (Mondays)

---

## Setup (Ubuntu)

### 1) System packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git sqlite3
````

### 2) Install + venv

```bash
mkdir -p /opt/portfolio-telegram-analytics/var/uploads
mkdir -p /opt/portfolio-telegram-analytics/var/reports
mkdir -p /opt/portfolio-telegram-analytics/var/cache
```

### 3) Create directories

```bash
mkdir -p /opt/portfolio-telegram-analytics/var/uploads
mkdir -p /opt/portfolio-telegram-analytics/var/reports
mkdir -p /opt/portfolio-telegram-analytics/var/cache
```

### 4) Environment variables

Create `.env` in repo root:

```bash
TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
DATABASE_URL=sqlite:////opt/portfolio-telegram-analytics/var/app.sqlite3
```

### 5) Initialize DB

Run your migration once:

```bash
source .venv/bin/activate
python -m portfolio_app.migrate
```
---

## Telegram usage

Commands:

* `/start` – intro
* `/upload` – instructions
* Upload CSV as a **document attachment**
* `/run` – queue analysis job
* `/report` – fetch latest job output + charts
* `/weekly on` / `/weekly off` – enable/disable weekly Monday summary

