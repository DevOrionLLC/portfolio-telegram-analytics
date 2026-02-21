from __future__ import annotations

from dataclasses import dataclass
import pandas as pd


@dataclass
class HoldingsParsed:
    items: list[tuple[str, float]]  # (ticker, quantity/shares)
    as_of_date: str | None          # YYYY-MM-DD
    warnings: list[str]


def read_csv_bytes(data: bytes) -> pd.DataFrame:
    """
    Robust CSV reader from bytes.
    """
    try:
        return pd.read_csv(pd.io.common.BytesIO(data))
    except Exception:
        return pd.read_csv(pd.io.common.BytesIO(data), encoding="latin-1")


def _norm_col(c: str) -> str:
    return (
        str(c)
        .strip()
        .lower()
        .replace(" ", "")
        .replace("-", "")
        .replace("_", "")
    )


def _find_col(df: pd.DataFrame, wanted: str, aliases: list[str]) -> str | None:
    want_norms = {_norm_col(wanted)} | {_norm_col(a) for a in aliases}
    for c in df.columns:
        if _norm_col(c) in want_norms:
            return str(c)
    return None


def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(",", ""), errors="coerce")


def parse_positions_snapshot(df: pd.DataFrame) -> HoldingsParsed:
    """
    Parse a positions snapshot CSV.

    Required columns:
      - as_of_date
      - ticker
      - quantity
      - price
      - market_value

    Optional columns (accepted if present):
      - cost_basis
      - sector
      - asset_class
      - account_name

    Behavior:
      - Aggregates duplicate tickers (sum quantity, sum market_value).
      - Warns if MV differs from Q*P by >5% when price exists.
      - Warns if holdings > 50.
    """
    warnings: list[str] = []

    c_asof = _find_col(df, "as_of_date", aliases=["asofdate", "asof", "date", "snapshot_date"])
    c_tkr = _find_col(df, "ticker", aliases=["symbol", "security", "instrument"])
    c_qty = _find_col(df, "quantity", aliases=["qty", "shares", "units", "position"])
    c_px = _find_col(df, "price", aliases=["last_price", "unit_price", "close"])
    c_mv = _find_col(df, "market_value", aliases=["marketvalue", "mv", "value", "position_value"])

    missing = [name for name, col in [
        ("as_of_date", c_asof),
        ("ticker", c_tkr),
        ("quantity", c_qty),
        ("price", c_px),
        ("market_value", c_mv),
    ] if col is None]

    if missing:
        raise ValueError(
            "Missing required columns: " + ", ".join(missing) +
            ". Required: as_of_date, ticker, quantity, price, market_value"
        )

    out = df.copy()

    # as_of_date: choose most common non-null date
    as_of_date: str | None = None
    try:
        dts = pd.to_datetime(out[c_asof], errors="coerce").dropna()
        if not dts.empty:
            as_of_date = str(dts.dt.date.mode().iloc[0])
    except Exception:
        warnings.append("Could not parse as_of_date values; continuing.")

    # ticker cleanup
    out["_ticker"] = out[c_tkr].astype(str).str.strip().str.upper()
    out = out[out["_ticker"].notna() & (out["_ticker"] != "")]

    # numeric
    out["_qty"] = _to_num(out[c_qty])
    out["_px"] = _to_num(out[c_px])
    out["_mv"] = _to_num(out[c_mv])

    before = len(out)
    out = out[out["_qty"].notna()]
    dropped = before - len(out)
    if dropped:
        warnings.append(f"Dropped {dropped} rows missing quantity.")

    if out.empty:
        return HoldingsParsed(items=[], as_of_date=as_of_date, warnings=warnings + ["No valid rows after cleaning."])

    # aggregate by ticker
    agg = out.groupby("_ticker", as_index=False).agg(
        quantity=("_qty", "sum"),
        market_value=("_mv", "sum"),
        price=("_px", "last"),  # for sanity check only
    )

    if len(agg) > 50:
        warnings.append(f"Holdings count is {len(agg)} (>50). Supported up to 50; results may be slow.")

    # MV sanity check when possible
    for _, r in agg.iterrows():
        t = str(r["_ticker"])
        q = float(r["quantity"]) if pd.notna(r["quantity"]) else None
        p = float(r["price"]) if pd.notna(r["price"]) else None
        mv = float(r["market_value"]) if pd.notna(r["market_value"]) else None
        if q is None or p is None or mv is None:
            continue
        est = q * p
        if abs(est) > 0:
            diff = abs(mv - est) / max(1.0, abs(est))
            if diff > 0.05:
                warnings.append(
                    f"{t}: market_value differs from quantity*price by ~{diff:.1%} "
                    f"(MV={mv:.2f}, Q*P={est:.2f})."
                )

    items = [(str(r["_ticker"]), float(r["quantity"])) for _, r in agg.iterrows() if pd.notna(r["quantity"])]

    return HoldingsParsed(items=items, as_of_date=as_of_date, warnings=warnings)