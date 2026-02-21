from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import settings

log = logging.getLogger("analytics")


@dataclass
class AnalysisResult:
    result: dict
    warnings: list[str]


def _daily_rf(rf_annual: float) -> float:
    return (1.0 + rf_annual) ** (1.0 / 252.0) - 1.0


def _max_drawdown(cum: pd.Series) -> float:
    peak = cum.cummax()
    dd = (cum / peak) - 1.0
    return float(dd.min())


def _metrics(returns: pd.Series) -> dict:
    returns = returns.dropna()
    if returns.empty:
        return {"return": None, "vol": None, "max_drawdown": None, "sharpe": None}

    total_return = float((1.0 + returns).prod() - 1.0)
    vol = float(returns.std(ddof=1) * np.sqrt(252.0))

    rf_d = _daily_rf(settings.RISK_FREE_RATE_ANNUAL)
    excess = returns - rf_d
    sharpe = float(excess.mean() / (returns.std(ddof=1) + 1e-12) * np.sqrt(252.0))

    cum = (1.0 + returns).cumprod()
    mdd = _max_drawdown(cum)

    return {"return": total_return, "vol": vol, "max_drawdown": mdd, "sharpe": sharpe}


def _align_price_frames(price_frames: dict[str, pd.DataFrame], use_adj: bool = True) -> pd.DataFrame:
    """
    Align prices across assets by date.
    Uses adj_close when present, otherwise close.
    Uses intersection of dates to avoid forward-fill bias.
    """
    px = []
    for t, df in price_frames.items():
        if df is None or df.empty:
            continue

        # ✅ FIX: fallback if adj_close missing
        if use_adj and "adj_close" in df.columns:
            s = df["adj_close"].rename(t)
        elif "close" in df.columns:
            s = df["close"].rename(t)
        else:
            log.warning("No usable price column for %s. Columns=%s", t, list(df.columns))
            continue

        px.append(s)

    if not px:
        return pd.DataFrame()

    out = pd.concat(px, axis=1).sort_index()
    out = out.dropna(axis=0, how="any")
    return out


def build_portfolio_returns(prices: pd.DataFrame, holdings: dict[str, float]) -> pd.Series:
    cols = [c for c in prices.columns if c in holdings]
    prices = prices[cols]
    if prices.empty:
        return pd.Series(dtype=float)

    shares = pd.Series({t: holdings[t] for t in cols}, dtype=float)
    values = prices.mul(shares, axis=1)
    total = values.sum(axis=1)
    return total.pct_change().dropna()


def contribution_by_asset(prices: pd.DataFrame, holdings: dict[str, float]) -> pd.Series:
    cols = [c for c in prices.columns if c in holdings]
    prices = prices[cols]
    shares = pd.Series({t: holdings[t] for t in cols}, dtype=float)
    values = prices.mul(shares, axis=1)
    total = values.sum(axis=1)
    w = values.div(total, axis=0)
    asset_rets = prices.pct_change()
    contrib = (w.shift(1) * asset_rets).sum(axis=0).sort_values(ascending=False)
    return contrib


def tsla_concentration(prices: pd.DataFrame, holdings: dict[str, float]) -> dict:
    if "TSLA" not in holdings or "TSLA" not in prices.columns:
        return {"tsla_weight": 0.0, "note": "TSLA not present or missing price data."}

    last_prices = prices.iloc[-1]
    values = {t: float(holdings[t] * last_prices[t]) for t in prices.columns if t in holdings}
    total = sum(values.values()) or 1.0
    tsla_w = values.get("TSLA", 0.0) / total

    asset_rets = prices.pct_change().dropna()
    cov = asset_rets.cov()
    weights = pd.Series({t: values[t] / total for t in values.keys()})
    cov = cov.loc[weights.index, weights.index]
    port_var = float(weights.T @ cov @ weights)
    if port_var <= 0:
        return {"tsla_weight": float(tsla_w), "variance_share": None}

    sigma_w = cov @ weights
    tsla_var_contrib = float(weights["TSLA"] * sigma_w["TSLA"])
    variance_share = tsla_var_contrib / port_var
    return {"tsla_weight": float(tsla_w), "variance_share": float(variance_share)}


def rebalance_tsla_static(holdings: dict[str, float], prices_last: pd.Series) -> tuple[dict[str, float], str | None]:
    """
    Reduce TSLA shares by 25%, redistribute freed value pro-rata to others.
    """
    if "TSLA" not in holdings or "TSLA" not in prices_last.index:
        return holdings.copy(), "TSLA not present (or missing price data); rebalance skipped."

    new = holdings.copy()
    tsla_shares_old = float(new["TSLA"])
    tsla_shares_new = tsla_shares_old * 0.75
    freed_shares = tsla_shares_old - tsla_shares_new
    freed_value = freed_shares * float(prices_last["TSLA"])
    new["TSLA"] = tsla_shares_new

    other = {t: q for t, q in new.items() if t != "TSLA" and t in prices_last.index}
    if not other:
        return new, "No other holdings with price data to redistribute to."

    other_values = {t: float(other[t] * prices_last[t]) for t in other}
    total_other = sum(other_values.values())
    if total_other <= 0:
        return new, "Other holdings total value is zero; cannot redistribute."

    for t in other:
        w = other_values[t] / total_other
        add_value = freed_value * w
        add_shares = add_value / float(prices_last[t])
        new[t] = float(new[t]) + float(add_shares)

    return new, None


def run_analysis(
    holdings_items: list[tuple[str, float]],
    price_frames: dict[str, pd.DataFrame],
    benchmarks: dict[str, pd.DataFrame],
) -> AnalysisResult:
    warnings: list[str] = []
    holdings = {t: q for t, q in holdings_items}

    px = _align_price_frames(price_frames, use_adj=True)
    if px.empty:
        return AnalysisResult(
            result={"error": "No aligned price data across holdings (after dropping missing dates)."},
            warnings=warnings,
        )

    port_rets = build_portfolio_returns(px, holdings)
    port_metrics = _metrics(port_rets)

    bench_out = {}
    for name, df in benchmarks.items():
        if df is None or df.empty:
            continue
        bpx = df["adj_close"] if "adj_close" in df.columns else df["close"]
        bpx = bpx.reindex(px.index).dropna()
        brets = bpx.pct_change().dropna()
        bench_out[name] = {"metrics": _metrics(brets), "ticker": name}

    tsla = tsla_concentration(px, holdings)

    reb_holdings, reb_warn = rebalance_tsla_static(holdings, px.iloc[-1])
    if reb_warn:
        warnings.append(reb_warn)

    reb_rets = build_portfolio_returns(px, reb_holdings)
    reb_metrics = _metrics(reb_rets)

    try:
        contrib = contribution_by_asset(px, holdings)
        top_pos = contrib.head(5).to_dict()
        top_neg = contrib.tail(5).to_dict()
    except Exception as e:
        warnings.append(f"Contribution calc failed: {e}")
        top_pos, top_neg = {}, {}

    result = {
        "window": {
            "start": str(px.index.min().date()),
            "end": str(px.index.max().date()),
            "days": int(len(px.index)),
        },
        "portfolio": {"metrics": port_metrics},
        "benchmarks": bench_out,
        "tsla": tsla,
        "rebalance": {
            "tsla_reduction": 0.25,
            "metrics_before": port_metrics,
            "metrics_after": reb_metrics,
        },
        "contributors": {"top_positive": top_pos, "top_negative": top_neg},
        "warnings": warnings,
    }
    return AnalysisResult(result=result, warnings=warnings)