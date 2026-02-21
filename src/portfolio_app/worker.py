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
from .ingestion import read_csv_bytes, parse_positions_snapshot
from .market_data import fetch_many, fetch_prices
from .analytics import run_analysis, build_portfolio_returns, rebalance_tsla_static, _align_price_frames
from .plots import plot_cumulative, plot_drawdown
from .config import settings

log = logging.getLogger("worker")


def _load_upload_bytes(upload: Upload) -> bytes:
    return Path(upload.stored_path).read_bytes()


def _benchmarks(months: int) -> tuple[dict[str, pd.DataFrame], list[str]]:
    out: dict[str, pd.DataFrame] = {}
    warns: list[str] = []
    for t in (settings.BENCHMARK_SP500, settings.BENCHMARK_R2000):
        df, w = fetch_prices(t, months)
        if w:
            warns.append(w)
        if df is not None:
            out[t] = df
    return out, warns


def _render_plots(
    job_id: int,
    holdings: dict[str, float],
    price_frames: dict[str, pd.DataFrame],
    benchmarks: dict[str, pd.DataFrame],
) -> list[str]:
    px = _align_price_frames(price_frames, use_adj=True)
    if px.empty:
        return []

    port_rets = build_portfolio_returns(px, holdings)
    reb_holdings, _ = rebalance_tsla_static(holdings, px.iloc[-1])
    reb_rets = build_portfolio_returns(px, reb_holdings)

    b_rets_map = {}
    for t, df in benchmarks.items():
        if df is None or df.empty:
            continue
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

    parsed = parse_positions_snapshot(df)
    holdings = dict(parsed.items)
    tickers = list(holdings.keys())

    price_frames, warns_prices = fetch_many(tickers, settings.HISTORY_MONTHS)
    bench_frames, warns_bench = _benchmarks(settings.HISTORY_MONTHS)

    result = run_analysis(parsed.items, price_frames, bench_frames)

    all_warns = parsed.warnings + warns_prices + warns_bench + result.warnings
    result.result["warnings"] = all_warns
    if parsed.as_of_date:
        result.result["snapshot_as_of_date"] = parsed.as_of_date
    result.result["holdings_count"] = len(parsed.items)

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