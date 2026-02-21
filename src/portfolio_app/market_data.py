from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import pandas as pd
import yfinance as yf

from .config import settings

log = logging.getLogger("market")


def _cache_path(ticker: str) -> Path:
    p = Path(settings.CACHE_DIR) / f"prices_{ticker}.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _end_date() -> dt.date:
    # use "today" and rely on market calendar implicitly (yfinance returns last trading day)
    return dt.date.today()


def _start_date(months: int) -> dt.date:
    # rough "3 months" as ~92 days; yfinance uses calendar dates
    return _end_date() - dt.timedelta(days=31 * months)


def fetch_prices(ticker: str, months: int) -> tuple[pd.DataFrame | None, str | None]:
    """
    Returns (df, warning). df has Date index and columns: adj_close, close
    """
    ticker = ticker.strip()
    cache = _cache_path(ticker)

    # cache hit within same day
    if cache.exists():
        try:
            df = pd.read_parquet(cache)
            if not df.empty and df.index.max().date() >= (_end_date() - dt.timedelta(days=1)):
                return df, None
        except Exception:
            pass

    start = _start_date(months)
    end = _end_date() + dt.timedelta(days=1)

    try:
        data = yf.download(ticker, start=start.isoformat(), end=end.isoformat(), progress=False, auto_adjust=False)
        if data is None or data.empty:
            return None, f"{ticker}: no price data returned."
        data = data.rename(columns=str.lower)
        out = pd.DataFrame(index=pd.to_datetime(data.index))
        out["close"] = data["close"]
        out["adj_close"] = data["adj close"] if "adj close" in data.columns else data["close"]
        out = out.dropna()
        if out.empty:
            return None, f"{ticker}: price data empty after cleaning."
        out.to_parquet(cache, index=True)
        return out, None
    except Exception as e:
        return None, f"{ticker}: price fetch failed: {e}"


def fetch_many(tickers: list[str], months: int) -> tuple[dict[str, pd.DataFrame], list[str]]:
    frames: dict[str, pd.DataFrame] = {}
    warnings: list[str] = []
    for t in tickers:
        df, w = fetch_prices(t, months)
        if w:
            warnings.append(w)
        if df is not None:
            frames[t] = df
    return frames, warnings