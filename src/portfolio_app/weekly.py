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


def _require_bot_token() -> str:
    token = (settings.TELEGRAM_BOT_TOKEN or "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set (weekly sender needs it).")
    return token


def _fmt_pct(x: float | None) -> str:
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return "n/a"
    return f"{x:.2%}"


def _fmt_money(x: float | None) -> str:
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return "n/a"
    return f"${x:,.2f}"


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


def _weights_at_last(px_last: pd.Series, holdings: dict[str, float]) -> dict[str, float]:
    cols = [c for c in px_last.index if c in holdings]
    if not cols:
        return {}
    vals = {t: float(px_last[t]) * float(holdings[t]) for t in cols}
    total = float(sum(vals.values()))
    if total <= 0:
        return {}
    return {t: v / total for t, v in vals.items()}


def _weekly_contrib(px: pd.DataFrame, holdings: dict[str, float], lookback_days: int = 5) -> pd.Series:
    cols = [c for c in px.columns if c in holdings]
    if not cols:
        return pd.Series(dtype=float)

    shares = pd.Series({t: float(holdings[t]) for t in cols}, dtype=float)
    values = px[cols].mul(shares, axis=1)
    total = values.sum(axis=1)

    w = values.div(total, axis=0).replace([np.inf, -np.inf], np.nan)
    r = px[cols].pct_change()

    contrib_daily = w.shift(1) * r
    contrib_tail = contrib_daily.tail(lookback_days)

    return contrib_tail.sum(axis=0).dropna().sort_values(ascending=False)


def _bench_return_1w(ticker: str, idx: pd.DatetimeIndex, lookback_days: int = 5) -> tuple[float | None, str | None]:
    df, w = fetch_prices(ticker, settings.HISTORY_MONTHS)
    if df is None or df.empty:
        return None, w or f"{ticker}: no price data returned."
    s = df["adj_close"] if "adj_close" in df.columns else df["close"]
    s = s.reindex(idx).dropna()
    if len(s) <= lookback_days:
        return None, w or f"{ticker}: insufficient overlap for 1W return."
    return _period_return(s, lookback_days), w


@dataclass
class WeeklySummary:
    as_of_date: str | None
    total_value: float | None
    portfolio_r_1w: float | None
    spy_r_1w: float | None
    iwm_r_1w: float | None
    tsla_weight: float | None
    top_movers: list[tuple[str, float]]
    bottom_movers: list[tuple[str, float]]
    notes: list[str]


def _compute_weekly(upload_path: str) -> WeeklySummary:
    notes: list[str] = []

    data = Path(upload_path).read_bytes()
    df = read_csv_bytes(data)
    parsed = parse_positions_snapshot(df)
    notes.extend(parsed.warnings)

    holdings_all = dict(parsed.items)

    # Exclude benchmarks from holdings (benchmarks are compared separately)
    bench_set = {settings.BENCHMARK_SP500, settings.BENCHMARK_R2000}
    excluded = sorted([t for t in holdings_all.keys() if t in bench_set])
    holdings = {t: q for t, q in holdings_all.items() if t not in bench_set}
    if excluded:
        notes.append(
            f"Excluded benchmark tickers from holdings: {', '.join(excluded)} "
            f"(benchmarks are compared separately)."
        )

    tickers = list(holdings.keys())
    if not tickers:
        return WeeklySummary(
            as_of_date=parsed.as_of_date,
            total_value=None,
            portfolio_r_1w=None,
            spy_r_1w=None,
            iwm_r_1w=None,
            tsla_weight=None,
            top_movers=[],
            bottom_movers=[],
            notes=notes + ["No holdings found after filtering."],
        )

    price_frames, warn_prices = fetch_many(tickers, settings.HISTORY_MONTHS)
    notes.extend([w for w in warn_prices if w])

    px = _align_price_frames(price_frames, use_adj=True)
    if px.empty:
        return WeeklySummary(
            as_of_date=parsed.as_of_date,
            total_value=None,
            portfolio_r_1w=None,
            spy_r_1w=None,
            iwm_r_1w=None,
            tsla_weight=None,
            top_movers=[],
            bottom_movers=[],
            notes=notes + ["No aligned price data across holdings (missing overlap)."],
        )

    values = _portfolio_value_series(px, holdings)
    total_value = float(values.iloc[-1]) if not values.empty else None

    portfolio_r_1w = _period_return(values, 5)

    spy_r_1w, w1 = _bench_return_1w(settings.BENCHMARK_SP500, px.index, 5)
    iwm_r_1w, w2 = _bench_return_1w(settings.BENCHMARK_R2000, px.index, 5)
    if w1:
        notes.append(w1)
    if w2:
        notes.append(w2)

    weights = _weights_at_last(px.iloc[-1], holdings)
    tsla_weight = float(weights["TSLA"]) if "TSLA" in weights else None

    contrib = _weekly_contrib(px, holdings, lookback_days=5)
    top_movers: list[tuple[str, float]] = []
    bottom_movers: list[tuple[str, float]] = []
    if not contrib.empty:
        top = contrib.head(3)
        bot = contrib.tail(3)
        top_movers = [(str(i), float(v)) for i, v in top.items()]
        bottom_movers = [(str(i), float(v)) for i, v in bot.items()]

    return WeeklySummary(
        as_of_date=parsed.as_of_date,
        total_value=total_value,
        portfolio_r_1w=portfolio_r_1w,
        spy_r_1w=spy_r_1w,
        iwm_r_1w=iwm_r_1w,
        tsla_weight=tsla_weight,
        top_movers=top_movers,
        bottom_movers=bottom_movers,
        notes=notes,
    )


def _format_weekly_message(s: WeeklySummary) -> str:
    lines: list[str] = []
    lines.append("📌 Weekly Portfolio Summary (last 5 trading days)")
    if s.as_of_date:
        lines.append(f"Snapshot as-of: {s.as_of_date}")
    lines.append("")

    lines.append(f"Total portfolio value: {_fmt_money(s.total_value)}")
    lines.append("")
    lines.append("Weekly returns:")
    lines.append(f"- Portfolio: {_fmt_pct(s.portfolio_r_1w)}")
    lines.append(f"- SPY:       {_fmt_pct(s.spy_r_1w)}")
    lines.append(f"- IWM:       {_fmt_pct(s.iwm_r_1w)}")
    lines.append("")

    lines.append("TSLA concentration:")
    lines.append(f"- TSLA weight: {_fmt_pct(s.tsla_weight)}")
    lines.append("")

    lines.append("Biggest movers (by contribution, last 5 trading days):")
    if s.top_movers:
        for t, v in s.top_movers:
            lines.append(f"- ↑ {t}: {_fmt_pct(v)}")
    else:
        lines.append("- n/a")

    if s.bottom_movers:
        for t, v in s.bottom_movers:
            lines.append(f"- ↓ {t}: {_fmt_pct(v)}")

    brief = [x for x in s.notes if x][:5]
    if brief:
        lines.append("")
        lines.append("Notes:")
        for n in brief:
            lines.append(f"- {n}")

    return "\n".join(lines)


async def main() -> None:
    setup_logging()
    bot = Bot(token=_require_bot_token())

    with SessionLocal() as db:
        users = db.query(User).filter(User.weekly_enabled == True).all()

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

    summary = _compute_weekly(last_upload.stored_path)
    msg = _format_weekly_message(summary)

    await bot.send_message(chat_id=int(user.telegram_chat_id), text=msg)
    log.info("Weekly summary sent to chat_id=%s (upload_id=%s)", user.telegram_chat_id, last_upload.id)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())