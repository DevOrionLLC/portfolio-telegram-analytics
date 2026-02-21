# Portfolio Telegram Analytics

## Overview

This project is a read-only portfolio analytics system that processes uploaded portfolio snapshots and generates descriptive performance reports. It retrieves recent market data using Yahoo Finance with a free Stooq fallback to ensure reliable coverage without paid API dependencies. The system computes key portfolio and benchmark metrics and delivers results with clear visual charts for easier interpretation. The design prioritizes stability, transparency, and ease of use.
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

