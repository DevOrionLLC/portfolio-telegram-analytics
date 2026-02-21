from __future__ import annotations

from pathlib import Path
import matplotlib.pyplot as plt
import pandas as pd


def _ensure_parent(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)


def plot_cumulative(returns_map: dict[str, pd.Series], out_path: Path) -> None:
    _ensure_parent(out_path)
    plt.figure()
    for name, r in returns_map.items():
        r = r.dropna()
        if r.empty:
            continue
        cum = (1.0 + r).cumprod()
        plt.plot(cum.index, cum.values, label=name)
    plt.legend()
    plt.title("Cumulative Returns (Trailing Window)")
    plt.xlabel("Date")
    plt.ylabel("Growth of $1")
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()


def plot_drawdown(returns_map: dict[str, pd.Series], out_path: Path) -> None:
    _ensure_parent(out_path)
    plt.figure()
    for name, r in returns_map.items():
        r = r.dropna()
        if r.empty:
            continue
        cum = (1.0 + r).cumprod()
        peak = cum.cummax()
        dd = (cum / peak) - 1.0
        plt.plot(dd.index, dd.values, label=name)
    plt.legend()
    plt.title("Drawdown")
    plt.xlabel("Date")
    plt.ylabel("Drawdown")
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()