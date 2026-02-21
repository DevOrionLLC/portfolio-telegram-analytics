from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from telegram import Bot

from .logging_setup import setup_logging
from .config import settings
from .db import SessionLocal
from .models import User, Upload
from .ingestion import read_csv_bytes, parse_positions_snapshot
from .market_data import fetch_many, fetch_prices
from .analytics import _align_price_frames

log = logging.getLogger("weekly")


# ----------------------------
# Formatting helpers
# ----------------------------

def _fmt_pct(x: float | None) -> str:
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return "n/a"
    return f"{x:.2%}"


def _fmt_money(x: float | None) -> str:
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return "n/a"
    return f"${x:,.2f}"


def _fmt_num(x: float | None) -> str:
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return "n/a"
    return f"{x:,.2f}"


# ----------------------------
# Finance helpers
# ----------------------------

def _daily_rf(rf_annual: float) -> float:
    # Annual -> daily (252 trading days)
    return (1.0 + rf_annual) ** (1.0 / 252.0) - 1.0


def _portfolio_value_series(px: pd.DataFrame, holdings: dict[str, float]) -> pd.Series:
    cols = [c for c in px.columns if c in holdings]
    if not cols:
        return pd.Series(dtype=float)
    shares = pd.Series({t: float(holdings[t]) for t in cols}, dtype=float)
    values = px[cols].mul(shares, axis=1)
    return values.sum(axis=1)


def _period_return(values: pd.Series, lookback_trading_days: int) -> float | None:
    values = values.dropna()
    if len(values) <= lookback_trading_days:
        return None
    start = float(values.iloc[-(lookback_trading_days + 1)])
    end = float(values.iloc[-1])
    if start == 0:
        return None
    return (end / start) - 1.0


def _annualized_vol(rets: pd.Series) -> float | None:
    r = rets.dropna()
    if len(r) < 2:
        return None
    return float(r.std(ddof=1) * np.sqrt(252.0))


def _sharpe_annual(rets: pd.Series) -> float | None:
    r = rets.dropna()
    if len(r) < 10:
        return None
    rf_d = _daily_rf(settings.RISK_FREE_RATE_ANNUAL)
    excess = r - rf_d
    denom = r.std(ddof=1)
    if denom <= 1e-12:
        return None
    return float(excess.mean() / denom * np.sqrt(252.0))


def _contribution_12m(px: pd.DataFrame, holdings: dict[str, float]) -> pd.Series:
    """
    Approx contribution over window = sum_t (w_{t-1} * r_t) by asset.
    """
    cols = [c for c in px.columns if c in holdings]
    if not cols:
        return pd.Series(dtype=float)

    shares = pd.Series({t: float(holdings[t]) for t in cols}, dtype=float)
    values = px[cols].mul(shares, axis=1)
    total = values.sum(axis=1)
    if (total <= 0).all():
        return pd.Series(dtype=float)

    w = values.div(total, axis=0)
    asset_rets = px[cols].pct_change()
    contrib_daily = w.shift(1) * asset_rets
    return contrib_daily.sum(axis=0).sort_values(ascending=False)


def _concentration_alerts(px_last: pd.Series, holdings: dict[str, float]) -> list[str]:
    cols = [c for c in px_last.index if c in holdings]
    if not cols:
        return ["No concentration computed (missing prices)."]

    values = {t: float(px_last[t]) * float(holdings[t]) for t in cols}
    total = float(sum(values.values()))
    if total <= 0:
        return ["No concentration computed (zero total value)."]

    weights = {t: v / total for t, v in values.items()}
    top = sorted(weights.items(), key=lambda kv: kv[1], reverse=True)

    alerts: list[str] = []
    top1_t, top1_w = top[0]
    if top1_w >= 0.25:
        alerts.append(f"Top holding {top1_t} is {_fmt_pct(top1_w)} of portfolio (≥ 25%).")

    top3_w = sum(w for _, w in top[:3])
    if top3_w >= 0.60:
        alerts.append(f"Top 3 holdings are {_fmt_pct(top3_w)} of portfolio (≥ 60%).")

    if "TSLA" in weights and weights["TSLA"] >= 0.20:
        alerts.append(f"TSLA concentration is {_fmt_pct(weights['TSLA'])} (≥ 20%).")

    if not alerts:
        alerts.append("No major concentration alerts (top holding < 25%, top 3 < 60%).")

    return alerts


@dataclass
class ExecWeekly:
    as_of_date: str | None
    total_value: float | None
    r_1w: float | None
    r_1m: float | None
    r_3m: float | None
    r_12m: float | None
    vol_12m: float | None
    sharpe_12m: float | None
    top_contrib: tuple[str, float] | None
    bottom_contrib: tuple[str, float] | None
    conc_alerts: list[str]
    bench_ticker: str
    bench_r_12m: float | None
    bench_vol_12m: float | None
    bench_sharpe_12m: float | None
    notes: list[str]


def _compute_exec_weekly(upload_path: str) -> ExecWeekly:
    notes: list[str] = []

    data = Path(upload_path).read_bytes()
    df = read_csv_bytes(data)
    parsed = parse_positions_snapshot(df)
    notes.extend(parsed.warnings)

    holdings = dict(parsed.items)
    tickers = list(holdings.keys())

    if not tickers:
        return ExecWeekly(
            as_of_date=parsed.as_of_date,
            total_value=None,
            r_1w=None, r_1m=None, r_3m=None, r_12m=None,
            vol_12m=None, sharpe_12m=None,
            top_contrib=None, bottom_contrib=None,
            conc_alerts=["No holdings found in snapshot."],
            bench_ticker=settings.BENCHMARK_R2000,
            bench_r_12m=None, bench_vol_12m=None, bench_sharpe_12m=None,
            notes=notes + ["No holdings found in snapshot."],
        )

    price_frames, warn_prices = fetch_many(tickers, settings.HISTORY_MONTHS)
    notes.extend([w for w in warn_prices if w])

    px = _align_price_frames(price_frames, use_adj=True)
    if px.empty:
        return ExecWeekly(
            as_of_date=parsed.as_of_date,
            total_value=None,
            r_1w=None, r_1m=None, r_3m=None, r_12m=None,
            vol_12m=None, sharpe_12m=None,
            top_contrib=None, bottom_contrib=None,
            conc_alerts=["No aligned price data across holdings (missing overlap)."],
            bench_ticker=settings.BENCHMARK_R2000,
            bench_r_12m=None, bench_vol_12m=None, bench_sharpe_12m=None,
            notes=notes + ["No aligned price data across holdings (missing overlap)."],
        )

    values = _portfolio_value_series(px, holdings)
    total_value = float(values.iloc[-1]) if not values.empty else None

    # Trading-day approximations
    r_1w = _period_return(values, 5)
    r_1m = _period_return(values, 21)
    r_3m = _period_return(values, 63)
    r_12m = _period_return(values, 252)

    # 12M risk
    values_12m = values.iloc[-252:] if len(values) > 252 else values
    rets_12m = values_12m.pct_change().dropna()
    vol_12m = _annualized_vol(rets_12m)
    sharpe_12m = _sharpe_annual(rets_12m)

    # Attribution over 12M
    px_12m = px.iloc[-252:] if len(px) > 252 else px
    contrib = _contribution_12m(px_12m, holdings)
    top_contrib = bottom_contrib = None
    if not contrib.empty:
        top_contrib = (str(contrib.index[0]), float(contrib.iloc[0]))
        bottom_contrib = (str(contrib.index[-1]), float(contrib.iloc[-1]))

    conc_alerts = _concentration_alerts(px.iloc[-1], holdings)

    # Benchmark: Russell 2000 proxy only (per requirement)
    bench_ticker = settings.BENCHMARK_R2000
    bench_r_12m = bench_vol_12m = bench_sharpe_12m = None
    try:
        bdf, bw = fetch_prices(bench_ticker, settings.HISTORY_MONTHS)
        if bw:
            notes.append(bw)
        if bdf is not None and not bdf.empty:
            bpx = bdf["adj_close"] if "adj_close" in bdf.columns else bdf["close"]
            bpx = bpx.reindex(px.index).dropna()
            if len(bpx) >= 10:
                brets = bpx.pct_change().dropna()
                brets_12m = brets.iloc[-252:] if len(brets) > 252 else brets
                if not brets_12m.empty:
                    bench_r_12m = float((1.0 + brets_12m).prod() - 1.0)
                    bench_vol_12m = _annualized_vol(brets_12m)
                    bench_sharpe_12m = _sharpe_annual(brets_12m)
    except Exception as e:
        notes.append(f"Benchmark fetch failed: {e}")

    return ExecWeekly(
        as_of_date=parsed.as_of_date,
        total_value=total_value,
        r_1w=r_1w, r_1m=r_1m, r_3m=r_3m, r_12m=r_12m,
        vol_12m=vol_12m,
        sharpe_12m=sharpe_12m,
        top_contrib=top_contrib,
        bottom_contrib=bottom_contrib,
        conc_alerts=conc_alerts,
        bench_ticker=bench_ticker,
        bench_r_12m=bench_r_12m,
        bench_vol_12m=bench_vol_12m,
        bench_sharpe_12m=bench_sharpe_12m,
        notes=notes,
    )


def _format_exec_message(s: ExecWeekly) -> str:
    lines: list[str] = []
    lines.append("🧾 Weekly Executive Summary")
    if s.as_of_date:
        lines.append(f"As-of snapshot: {s.as_of_date}")
    lines.append("")

    lines.append(f"Total portfolio value: {_fmt_money(s.total_value)}")
    lines.append("")

    lines.append("Returns:")
    lines.append(f"- 1W:  {_fmt_pct(s.r_1w)}")
    lines.append(f"- 1M:  {_fmt_pct(s.r_1m)}")
    lines.append(f"- 3M:  {_fmt_pct(s.r_3m)}")
    lines.append(f"- 12M: {_fmt_pct(s.r_12m)}")
    lines.append("")

    lines.append("Risk (12M):")
    lines.append(f"- Volatility (ann.): {_fmt_pct(s.vol_12m)}")
    lines.append(f"- Sharpe ratio:      {_fmt_num(s.sharpe_12m)}")
    lines.append("")

    lines.append("Attribution (12M):")
    if s.top_contrib:
        lines.append(f"- Largest contributor: {s.top_contrib[0]} ({_fmt_pct(s.top_contrib[1])})")
    else:
        lines.append("- Largest contributor: n/a")
    if s.bottom_contrib:
        lines.append(f"- Largest detractor:   {s.bottom_contrib[0]} ({_fmt_pct(s.bottom_contrib[1])})")
    else:
        lines.append("- Largest detractor:   n/a")
    lines.append("")

    lines.append("Concentration alerts:")
    for a in s.conc_alerts[:5]:
        lines.append(f"- {a}")
    lines.append("")

    lines.append(f"Benchmark comparison (Russell 2000 proxy: {s.bench_ticker}):")
    lines.append(f"- 12M Return: {_fmt_pct(s.bench_r_12m)}")
    lines.append(f"- 12M Vol (ann.): {_fmt_pct(s.bench_vol_12m)}")
    lines.append(f"- 12M Sharpe: {_fmt_num(s.bench_sharpe_12m)}")

    # Keep notes short
    if s.notes:
        brief = [x for x in s.notes if x][:5]
        if brief:
            lines.append("")
            lines.append("Notes:")
            for n in brief:
                lines.append(f"- {n}")

    return "\n".join(lines)


# ----------------------------
# Runner (called by systemd timer)
# ----------------------------

async def main() -> None:
    setup_logging()
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)

    with SessionLocal() as db:
        users = db.query(User).filter(User.weekly_enabled == True).all()  # noqa: E712

    if not users:
        log.info("No users with weekly_enabled=1. Nothing to send.")
        return

    for u in users:
        try:
            await _send_user_weekly(bot, u)
        except Exception as e:
            log.exception("Weekly failed for user=%s: %s", u.telegram_chat_id, e)


async def _send_user_weekly(bot: Bot, user: User) -> None:
    with SessionLocal() as db:
        last_upload = (
            db.query(Upload)
            .filter(Upload.user_id == user.id)
            .order_by(Upload.id.desc())
            .first()
        )

    if not last_upload:
        log.info("Skip weekly for chat_id=%s: no uploads.", user.telegram_chat_id)
        return

    # Compute + send
    summary = _compute_exec_weekly(last_upload.stored_path)
    msg = _format_exec_message(summary)

    await bot.send_message(chat_id=int(user.telegram_chat_id), text=msg)
    log.info("Weekly summary sent to chat_id=%s (upload_id=%s)", user.telegram_chat_id, last_upload.id)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())