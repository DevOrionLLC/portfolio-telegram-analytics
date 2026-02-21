from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

from .config import settings

log = logging.getLogger("market")


def _cache_path(ticker: str) -> Path:
    # Use pickle cache to avoid pyarrow/fastparquet dependency.
    p = Path(settings.CACHE_DIR) / f"prices_{ticker.upper()}.pkl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _end_date() -> dt.date:
    return dt.date.today()


def _start_date(months: int) -> dt.date:
    return _end_date() - dt.timedelta(days=31 * months)


def _clean_yf_frame(raw: pd.DataFrame) -> pd.DataFrame | None:
    """
    Normalize yfinance output into DateTimeIndex + columns: close, adj_close
    """
    if raw is None or raw.empty:
        return None

    if isinstance(raw.columns, pd.MultiIndex):
        raw = raw.copy()
        raw.columns = [str(c[0]).lower() for c in raw.columns]

    cols = {str(c).lower(): c for c in raw.columns}
    close_col = cols.get("close")
    adj_col = cols.get("adj close") or cols.get("adjclose")

    if close_col is None:
        return None

    out = pd.DataFrame(index=pd.to_datetime(raw.index))
    out["close"] = pd.to_numeric(raw[close_col], errors="coerce")
    if adj_col is not None:
        out["adj_close"] = pd.to_numeric(raw[adj_col], errors="coerce")
    else:
        out["adj_close"] = out["close"]

    out = out.dropna()
    return None if out.empty else out


def _stooq_symbol(ticker: str) -> str | None:
    """
    Stooq symbols often look like aapl.us for US tickers.
    Extend later if you need non-US.
    """
    t = ticker.strip().upper()
    if not t or t.startswith("^"):
        return None
    if "." in t:
        return t.lower()
    return f"{t.lower()}.us"


def _fetch_stooq_daily(ticker: str, months: int) -> tuple[pd.DataFrame | None, str | None]:
    sym = _stooq_symbol(ticker)
    if sym is None:
        return None, f"{ticker}: stooq symbol not supported."

    url = f"https://stooq.com/q/d/l/?s={sym}&i=d"

    try:
        df = pd.read_csv(url)
    except Exception as e:
        return None, f"{ticker}: stooq fetch failed: {e}"

    if df is None or df.empty or "Date" not in df.columns:
        return None, f"{ticker}: stooq returned empty."

    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"]).set_index("Date").sort_index()

    if "Close" not in df.columns:
        return None, f"{ticker}: stooq missing Close."

    out = pd.DataFrame(index=df.index)
    out["close"] = pd.to_numeric(df["Close"], errors="coerce")
    out["adj_close"] = out["close"]  # simple fallback (no guaranteed adjusted)

    start = pd.Timestamp(_start_date(months))
    out = out.loc[out.index >= start].dropna()

    if out.empty:
        return None, f"{ticker}: stooq empty after filtering."
    return out, None


def fetch_prices(ticker: str, months: int) -> tuple[pd.DataFrame | None, str | None]:
    """
    Provider order:
      1) Yahoo Finance via yfinance
      2) Stooq CSV (free fallback)

    Returns (df, warning). df has Date index and columns: close, adj_close
    """
    t = ticker.strip().upper()
    cache = _cache_path(t)

    # cache hit within ~1 day
    if cache.exists():
        try:
            df = pd.read_pickle(cache)
            if not df.empty and df.index.max().date() >= (_end_date() - dt.timedelta(days=1)):
                return df, None
        except Exception:
            # Corrupt cache or old format; ignore
            pass

    start = _start_date(months)
    end = _end_date() + dt.timedelta(days=1)

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }
    )

    # 1) Yahoo download()
    try:
        raw = yf.download(
            t,
            start=start.isoformat(),
            end=end.isoformat(),
            progress=False,
            auto_adjust=False,
            threads=False,
        )
        out = _clean_yf_frame(raw)
        if out is not None:
            out.to_pickle(cache)
            return out, None
    except Exception as e:
        log.warning("Yahoo download failed for %s: %s", t, e)

    # 2) Yahoo history() fallback
    try:
        tk = yf.Ticker(t, session=session)
        raw2 = tk.history(
            period=f"{max(1, int(months))}mo",
            interval="1d",
            auto_adjust=False,
            actions=False,
        )
        out2 = _clean_yf_frame(raw2)
        if out2 is not None:
            out2.to_pickle(cache)
            return out2, None
    except Exception as e:
        log.warning("Yahoo history failed for %s: %s", t, e)

    # 3) Stooq fallback
    out3, w3 = _fetch_stooq_daily(t, months)
    if out3 is not None:
        out3.to_pickle(cache)
        return out3, (w3 or f"{t}: used stooq fallback.")

    return None, (w3 or f"{t}: no price data returned.")


def fetch_many(tickers: list[str], months: int) -> tuple[dict[str, pd.DataFrame], list[str]]:
    frames: dict[str, pd.DataFrame] = {}
    warnings: list[str] = []
    for t in tickers:
        df, w = fetch_prices(t, months)
        if w:
            warnings.append(w)
        if df is not None:
            frames[t.upper()] = df
    return frames, warnings