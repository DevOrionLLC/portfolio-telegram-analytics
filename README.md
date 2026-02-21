```markdown
# Portfolio Telegram Analytics (Read-Only)

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

## Inputs

### Portfolio CSV snapshot (positions)

Each row = one holding as of a specific date.

**Required columns**
- `as_of_date`
- `ticker`
- `quantity`
- `price`
- `market_value`

**Optional columns**
- `cost_basis`
- `sector`
- `asset_class`
- `account_name`

**Rules**
- Missing required fields → **warn and skip only that row**
- Continue processing remaining valid holdings
- Typically supports up to ~50 holdings easily

> Note: SPY/IWM are treated as **benchmarks**. If they appear in your CSV holdings, the system **excludes them from portfolio holdings by default** and still uses them for benchmark comparison.

---

## Market data

### Providers (free)

- Primary: Yahoo via `yfinance`
- Fallback: Stooq daily CSV (free)

### Window
- Lookback: last ~3 months (~63 trading days)
- End date: most recent trading day available from providers
- If a ticker lacks enough usable history or overlap → warn and skip where necessary

### Caching
To avoid extra dependencies like `pyarrow`, caching uses **Pandas Pickle (`.pkl`)** in:
- `var/cache/`

---

## Metrics (3-month trailing)

Computed for **Portfolio + SPY + IWM**:

- Total return
- Annualized volatility (from daily returns)
- Max drawdown
- Sharpe ratio (annual rf = **3%**, converted to daily)

---

## TSLA concentration analysis

At minimum:
- Current TSLA weight
- TSLA return vs portfolio return
- TSLA share of portfolio variance (covariance-based if stable; otherwise fallback to simpler stats)

---

## Static one-time rebalance simulation

- Reduce TSLA **shares by 25%** (e.g., 100 → 75)
- Freed cash is redistributed **pro-rata** to other holdings
- Holdings without price data are excluded from reallocation
- Compare original vs rebalanced portfolio over the same 3-month window

---

## Project layout (typical)

```

app/
analytics.py
config.py
db.py
ingestion.py
logging_setup.py
market_data.py
models.py
plots.py
telegram_bot.py
weekly.py
worker.py
var/
app.sqlite3
uploads/
reports/
cache/

````

---

## Setup (Ubuntu)

### 1) System packages
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git sqlite3
````

### 2) Install + venv

```bash
cd /opt/portfolio-telegram-analytics
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

If you don’t have `requirements.txt`, minimum:

```bash
pip install pandas numpy yfinance requests python-telegram-bot sqlalchemy pydantic-settings matplotlib
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
cat > .env << 'EOF'
TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
# TELEGRAM_CHAT_ID is not required for bot mode (chat_id comes from user).
# Optional override:
# DATABASE_URL=sqlite:////opt/portfolio-telegram-analytics/var/app.sqlite3
EOF
```

### 5) Initialize DB

Run your migration once:

```bash
source .venv/bin/activate
python -m app.migrate
```

---

## Running (manual)

Open two terminals.

### Terminal A: Worker

```bash
cd /opt/portfolio-telegram-analytics
source .venv/bin/activate
python -m app.worker
```

### Terminal B: Telegram bot

```bash
cd /opt/portfolio-telegram-analytics
source .venv/bin/activate
python -m app.telegram_bot
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

---

## Weekly summary (Mondays)

The weekly summary:

* Uses last **5 trading days**
* Portfolio vs SPY vs IWM weekly returns
* Current TSLA weight
* Biggest movers by contribution

### Scheduling options

**Option A: systemd timer (recommended)**
If you already use systemd timers, create a timer to run:

```bash
python -m app.weekly
```

**Option B: cron**
Example (Mondays at 9:00 AM):

```bash
0 9 * * 1 cd /opt/portfolio-telegram-analytics && . .venv/bin/activate && python -m app.weekly
```

---

## Troubleshooting

### Jobs stuck in `queued`

This means **the worker is not running or crashed**.

Check:

```bash
sudo systemctl status portfolio-worker.service --no-pager
sudo journalctl -u portfolio-worker.service -n 200 --no-pager
```

Common causes:

* Worker environment missing variables or wrong working directory
* Worker and bot using different `DATABASE_URL` (two different SQLite files)

### “No price data returned”

* Yahoo can rate-limit/block some VPS IPs.
* This project falls back to Stooq (free) automatically.
* Check warnings included in Telegram output.

### Charts not sent

* Confirm `var/reports/` is writable
* Confirm image files exist for the job folder

---

## Security & privacy

* This is **read-only**; no trading/execution.
* Uploaded CSVs are stored locally under `var/uploads/`.
* Keep your server locked down and your bot token private.

---

## Explicit exclusions

* No brokerage APIs
* No auto-trading
* No forecasting / ML / AI predictions
* No tax/commission/slippage modeling

---

## License / disclaimer

Use at your own risk. This software provides descriptive analytics and does not constitute investment advice.

```
::contentReference[oaicite:3]{index=3}
```
