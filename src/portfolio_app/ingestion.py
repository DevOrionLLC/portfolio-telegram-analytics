from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from typing import Iterable

import pandas as pd

log = logging.getLogger("ingestion")


@dataclass(frozen=True)
class Holdings:
    # list of (ticker, quantity)
    items: list[tuple[str, float]]
    warnings: list[str]


def normalize_ticker(x: str) -> str:
    x = (x or "").strip().upper()
    x = x.replace("\ufeff", "")  # BOM
    # very light normalization; extend as needed
    return x


def detect_columns(df: pd.DataFrame) -> dict[str, list[str]]:
    cols = [c for c in df.columns]
    lc = {c: c.lower().strip() for c in cols}

    ticker_candidates = [c for c in cols if any(k in lc[c] for k in ["ticker", "symbol", "security", "instrument"])]
    qty_candidates = [c for c in cols if any(k in lc[c] for k in ["qty", "quantity", "shares", "units", "position"])]

    return {"ticker": ticker_candidates, "qty": qty_candidates, "all": cols}


def read_csv_bytes(data: bytes) -> pd.DataFrame:
    # robust-ish: try utf-8 then latin-1
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return pd.read_csv(io.BytesIO(data), encoding=enc)
        except Exception:
            continue
    # last resort: let pandas guess
    return pd.read_csv(io.BytesIO(data))


def parse_holdings(df: pd.DataFrame, ticker_col: str, qty_col: str) -> Holdings:
    warnings: list[str] = []
    if ticker_col not in df.columns or qty_col not in df.columns:
        return Holdings(items=[], warnings=[f"Missing required columns: {ticker_col} / {qty_col}"])

    sub = df[[ticker_col, qty_col]].copy()
    sub.columns = ["ticker", "qty"]

    items: list[tuple[str, float]] = []
    for i, row in sub.iterrows():
        t = normalize_ticker(str(row["ticker"]))
        if not t or t.lower() == "nan":
            continue
        try:
            q = float(row["qty"])
        except Exception:
            warnings.append(f"Row {i}: could not parse quantity for {t}; skipped.")
            continue
        if q == 0:
            continue
        items.append((t, q))

    if not items:
        warnings.append("No holdings parsed (after cleaning).")

    # aggregate duplicates
    agg: dict[str, float] = {}
    for t, q in items:
        agg[t] = agg.get(t, 0.0) + q

    final = sorted(agg.items(), key=lambda x: x[0])
    return Holdings(items=final, warnings=warnings)