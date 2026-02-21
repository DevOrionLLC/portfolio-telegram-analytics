from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

import pandas as pd

from telegram import Bot

from .logging_setup import setup_logging
from .config import settings
from .db import SessionLocal
from .models import User, Upload
from .ingestion import read_csv_bytes, parse_holdings
from .market_data import fetch_many, fetch_prices
from .analytics import build_portfolio_returns, rebalance_tsla_static, _align_price_frames, tsla_concentration
# from .openclaw_hooks import post_wake

log = logging.getLogger("weekly")


def _last_n_trading_days(rets: pd.Series, n: int = 5) -> pd.Series:
    rets = rets.dropna()
    if len(rets) <= n:
        return rets
    return rets.iloc[-n:]


def _week_return(rets: pd.Series) -> float | None:
    rets = rets.dropna()
    if rets.empty:
        return None
    return float((1.0 + rets).prod() - 1.0)


async def main() -> None:
    setup_logging()
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)

    with SessionLocal() as db:
        users = db.query(User).filter(User.weekly_enabled == True).all()  # noqa: E712

    for u in users:
        try:
            await _send_user_weekly(bot, u)
        except Exception as e:
            log.warning("Weekly failed for user=%s: %s", u.telegram_chat_id, e)


async def _send_user_weekly(bot: Bot, user: User) -> None:
    with SessionLocal() as db:
        last_upload = (
            db.query(Upload)
            .filter(Upload.user_id == user.id)
            .order_by(Upload.id.desc())
            .first()
        )
    if not last_upload:
        return

    if not user.ticker_col or not user.qty_col:
        return

    data = Path(last_upload.stored_path).read_bytes()
    df = read_csv_bytes(data)
    holdings_parsed = parse_holdings(df, user.ticker_col, user.qty_col)
    holdings = dict(holdings_parsed.items)
    tickers = list(holdings.keys())

    price_frames, warn_prices = fetch_many(tickers, settings.HISTORY_MONTHS)
    px = _align_price_frames(price_frames, use_adj=True)
    if px.empty:
        return

    port_rets = build_portfolio_returns(px, holdings)
    w_port = _week_return(_last_n_trading_days(port_rets, 5))

    # benchmarks weekly
    bench = {}
    for t in (settings.BENCHMARK_SP500, settings.BENCHMARK_R2000):
        dfb, _ = fetch_prices(t, settings.HISTORY_MONTHS)
        if dfb is None:
            bench[t] = None
            continue
        s = dfb["adj_close"] if "adj_close" in dfb.columns else dfb["close"]
        s = s.reindex(px.index).dropna()
        brets = s.pct_change()
        bench[t] = _week_return(_last_n_trading_days(brets, 5))

    tsla = tsla_concentration(px, holdings)

    # top mover by contribution over last 5 days (rough)
    last5 = px.iloc[-6:]  # 5 returns
    last_rets = last5.pct_change().dropna()
    last_vals = (last5.iloc[-1] * pd.Series({k: v for k, v in holdings.items() if k in px.columns})).dropna()
    total = float(last_vals.sum()) or 1.0
    weights = last_vals / total
    contrib = (last_rets.mul(weights, axis=1)).sum(axis=0).sort_values(ascending=False)
    top_pos = contrib.head(1).to_dict()
    top_neg = contrib.tail(1).to_dict()

    def fmt(x):
        if x is None:
            return "n/a"
        return f"{x:.2%}"

    msg = (
        "🗓️ Weekly Summary (last 5 trading days)\n"
        f"- Portfolio: {fmt(w_port)}\n"
        f"- {settings.BENCHMARK_SP500}: {fmt(bench.get(settings.BENCHMARK_SP500))}\n"
        f"- {settings.BENCHMARK_R2000}: {fmt(bench.get(settings.BENCHMARK_R2000))}\n"
        f"- TSLA weight: {fmt(tsla.get('tsla_weight'))}\n"
        f"- Top contributor: {list(top_pos.items())[0] if top_pos else 'n/a'}\n"
        f"- Worst contributor: {list(top_neg.items())[0] if top_neg else 'n/a'}\n"
    )

    await bot.send_message(chat_id=int(user.telegram_chat_id), text=msg)
    # post_wake(f"Weekly summary sent to chat_id={user.telegram_chat_id}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())