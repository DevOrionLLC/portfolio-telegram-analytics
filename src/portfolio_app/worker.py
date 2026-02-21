from __future__ import annotations

import datetime as dt
import json
import logging
import time
from pathlib import Path

import pandas as pd

from .logging_setup import setup_logging
from .db import SessionLocal
from .models import Job, User, Upload
from .ingestion import read_csv_bytes, parse_holdings
from .market_data import fetch_many, fetch_prices
from .analytics import run_analysis, build_portfolio_returns, rebalance_tsla_static, _align_price_frames
from .plots import plot_cumulative, plot_drawdown
from .config import settings

log = logging.getLogger("worker")


def _load_upload_bytes(upload: Upload) -> bytes:
    return Path(upload.stored_path).read_bytes()


def _norm(s: str) -> str:
    return "".join(ch.lower() for ch in str(s).strip())


def _infer_ticker_and_qty_columns(df: pd.DataFrame) -> tuple[str | None, str | None]:
    cols = [str(c) for c in df.columns]
    if not cols:
        return None, None

    cols_norm = {_norm(c): c for c in cols}

    ticker_keys_priority = ["symbol", "ticker", "security", "instrument", "asset_symbol", "assetname", "asset_name"]
    qty_keys_priority = ["quantity", "qty", "shares", "units", "position", "amount"]

    ticker_col = None
    for key in ticker_keys_priority:
        for cnorm, corig in cols_norm.items():
            if cnorm == key or cnorm.replace(" ", "") == key:
                ticker_col = corig
                break
        if ticker_col:
            break
    if not ticker_col:
        for c in cols:
            cn = _norm(c)
            if "ticker" in cn or "symbol" in cn or cn.startswith("sym"):
                ticker_col = c
                break

    qty_col = None
    for key in qty_keys_priority:
        for cnorm, corig in cols_norm.items():
            if cnorm == key or cnorm.replace(" ", "") == key:
                qty_col = corig
                break
        if qty_col:
            break
    if not qty_col:
        for c in cols:
            cn = _norm(c)
            if "qty" in cn or "quant" in cn or "share" in cn or "unit" in cn:
                qty_col = c
                break

    return ticker_col, qty_col


def _benchmarks(months: int) -> tuple[dict[str, pd.DataFrame], list[str]]:
    out = {}
    warns = []
    for t in (settings.BENCHMARK_SP500, settings.BENCHMARK_R2000):
        df, w = fetch_prices(t, months)
        if w:
            warns.append(w)
        if df is not None:
            out[t] = df
    return out, warns


def _render_plots(job_id: int, holdings: dict[str, float], price_frames: dict[str, pd.DataFrame], benchmarks: dict[str, pd.DataFrame]) -> list[str]:
    px = _align_price_frames(price_frames, use_adj=True)
    if px.empty:
        return []

    port_rets = build_portfolio_returns(px, holdings)
    reb_holdings, _ = rebalance_tsla_static(holdings, px.iloc[-1])
    reb_rets = build_portfolio_returns(px, reb_holdings)

    b_rets_map = {}
    for t, df in benchmarks.items():
        s = df["adj_close"] if "adj_close" in df.columns else df["close"]
        s = s.reindex(px.index).dropna()
        b_rets_map[t] = s.pct_change()

    returns_map = {"Portfolio": port_rets, "Rebalanced": reb_rets}
    returns_map |= {f"Bench {k}": v for k, v in b_rets_map.items()}

    report_dir = Path(settings.REPORT_DIR) / f"job_{job_id}"
    report_dir.mkdir(parents=True, exist_ok=True)

    cum_path = report_dir / "cumulative.png"
    dd_path = report_dir / "drawdown.png"

    plot_cumulative(returns_map, cum_path)
    plot_drawdown({"Portfolio": port_rets, "Rebalanced": reb_rets}, dd_path)

    return [str(cum_path), str(dd_path)]


def run_forever(poll_seconds: int = 2) -> None:
    setup_logging()
    Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.REPORT_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.CACHE_DIR).mkdir(parents=True, exist_ok=True)

    log.info("Worker started. Polling for jobs…")

    while True:
        job = None
        with SessionLocal() as db:
            job = (
                db.query(Job)
                .filter(Job.status == "queued")
                .order_by(Job.id.asc())
                .first()
            )
            if job:
                job.status = "running"
                job.started_at = dt.datetime.utcnow()
                db.commit()
                db.refresh(job)

        if not job:
            time.sleep(poll_seconds)
            continue

        try:
            _run_job(job.id)
        except Exception as e:
            log.exception("Job failed: %s", e)
            with SessionLocal() as db:
                j = db.query(Job).filter(Job.id == job.id).one()
                j.status = "failed"
                j.error = str(e)
                j.finished_at = dt.datetime.utcnow()
                db.commit()


def _run_job(job_id: int) -> None:
    with SessionLocal() as db:
        job = db.query(Job).filter(Job.id == job_id).one()
        user = db.query(User).filter(User.id == job.user_id).one()
        upload = db.query(Upload).filter(Upload.id == job.upload_id).one()

    data = _load_upload_bytes(upload)
    df = read_csv_bytes(data)

    # ✅ Auto-fallback mapping if missing
    if not user.ticker_col or not user.qty_col:
        ticker_col, qty_col = _infer_ticker_and_qty_columns(df)
        if not ticker_col or not qty_col:
            raise RuntimeError(
                "Could not auto-detect ticker/quantity columns from CSV headers. "
                "Rename headers to include 'symbol' (or 'ticker') and 'quantity' (or 'shares') and upload again."
            )
        with SessionLocal() as db:
            u = db.query(User).filter(User.id == user.id).one()
            u.ticker_col = ticker_col
            u.qty_col = qty_col
            db.commit()
        # refresh local copy
        user.ticker_col = ticker_col
        user.qty_col = qty_col

    holdings_parsed = parse_holdings(df, user.ticker_col, user.qty_col)
    holdings = dict(holdings_parsed.items)
    tickers = list(holdings.keys())

    price_frames, warns_prices = fetch_many(tickers, settings.HISTORY_MONTHS)
    bench_frames, warns_bench = _benchmarks(settings.HISTORY_MONTHS)

    result = run_analysis(holdings_parsed.items, price_frames, bench_frames)

    all_warns = holdings_parsed.warnings + warns_prices + warns_bench + result.warnings
    result.result["warnings"] = all_warns

    report_paths = _render_plots(job_id, holdings, price_frames, bench_frames)

    with SessionLocal() as db:
        j = db.query(Job).filter(Job.id == job_id).one()
        j.status = "done"
        j.finished_at = dt.datetime.utcnow()
        j.result_json = json.dumps(result.result)
        j.report_paths_json = json.dumps(report_paths)
        db.commit()


if __name__ == "__main__":
    run_forever()