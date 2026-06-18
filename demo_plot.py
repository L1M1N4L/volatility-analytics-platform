"""
Offline demo — synthetic mean-reverting volatility run through the dashboard's own
analysis (`analyze` from iv_dashboard), rendered to a static PNG with matplotlib +
seaborn. No IB connection required; this just proves the charts/analysis populate.

    python demo_plot.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # file output, no display needed
import matplotlib.pyplot as plt
import seaborn as sns

from iv_dashboard import analyze  # reuse the real analysis pipeline

OUT = r"C:\My Files\CODING SHIT\Volatility Analytics Platform\demo_volatility_analysis.png"
BLUE, RED = "#4C72B0", "#C44E52"


def synth_log_ou(n: int, mu: float, kappa: float, sigma: float, rng) -> np.ndarray:
    """Log-space Ornstein-Uhlenbeck → strictly-positive, mean-reverting vol path."""
    log_mu, x, out = np.log(mu), np.log(mu), np.empty(n)
    for i in range(n):
        x += kappa * (log_mu - x) + sigma * rng.standard_normal()
        out[i] = np.exp(x)
    return out


def make_data(n: int = 504, seed: int = 7) -> tuple[pd.Series, pd.Series]:
    rng = np.random.default_rng(seed)
    iv = synth_log_ou(n, mu=0.16, kappa=0.03, sigma=0.07, rng=rng)
    # inject a couple of decaying vol spikes for realism
    for loc, jump in ((int(n * 0.35), 0.18), (int(n * 0.70), 0.12)):
        iv[loc:] += jump * np.exp(-np.arange(n - loc) / 12)
    # realized vol: correlated with IV but generally below it (positive risk premium)
    hv = np.clip(0.85 * iv + rng.normal(0, 0.012, n) - 0.008, 0.04, None)

    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    return pd.Series(iv, idx, name="implied_vol"), pd.Series(hv, idx, name="historical_vol")


def main() -> None:
    sns.set_theme(style="whitegrid")
    iv, hv = make_data()
    res = analyze(iv)
    df, ff, split = res["df"], res["fit_forward"], res["split"]
    high, low = res["high"], res["low"]

    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(19, 6))

    # Panel 1 — forward vs current
    ax1.scatter(df.current_vol, df.forward_30d_vol, s=18, alpha=0.5, color=BLUE, edgecolor="none")
    xr = np.linspace(df.current_vol.min(), df.current_vol.max(), 100)
    ax1.plot(xr, ff.slope * xr + ff.intercept, color=RED, lw=2.5, label=f"fit · R²={ff.rvalue ** 2:.3f}")
    lo = min(df.current_vol.min(), df.forward_30d_vol.min())
    hi = max(df.current_vol.max(), df.forward_30d_vol.max())
    ax1.plot([lo, hi], [lo, hi], "k--", lw=1.2, alpha=0.7, label="y = x (no change)")
    ax1.set(xlabel="Current implied vol", ylabel="30-day forward IV",
            title=f"Forward vs current\ny = {ff.slope:.3f}x + {ff.intercept:.3f}")
    ax1.legend(fontsize=9)

    # Panel 2 — regime split
    ax2.scatter(df.current_vol[high], df.vol_diff[high], s=18, alpha=0.5, color=RED, label="high-vol regime")
    ax2.scatter(df.current_vol[low], df.vol_diff[low], s=18, alpha=0.5, color=BLUE, label="low-vol regime")
    for mask, fit, color in ((high, res["fit_high"], RED), (low, res["fit_low"], BLUE)):
        if fit is not None:
            x = df.current_vol[mask]
            xr2 = np.linspace(x.min(), x.max(), 100)
            ax2.plot(xr2, fit.slope * xr2 + fit.intercept, color=color, lw=2.5)
    ax2.axhline(0, color="k", ls="--", lw=1, alpha=0.7)
    ax2.axvline(split, color="green", ls=":", lw=1.5, label=f"regime split = {split:.3f}")
    ax2.set(xlabel="Current implied vol", ylabel="Forward − current IV", title="Vol difference (regime analysis)")
    ax2.legend(fontsize=9)

    # Panel 3 — time series with bands
    ax3.plot(iv.index, iv, lw=1.3, label="Implied vol")
    ax3.plot(hv.index, hv, lw=1.1, alpha=0.8, label="Historical vol")
    ax3.axhline(iv.quantile(0.75), color="red", ls="--", alpha=0.6, label="75th pct")
    ax3.axhline(iv.quantile(0.25), color="green", ls="--", alpha=0.6, label="25th pct")
    ax3.axhline(iv.mean(), color="gray", lw=1.2, alpha=0.7, label="mean")
    ax3.scatter([iv.index[-1]], [iv.iloc[-1]], color=RED, s=90, zorder=5, label="current")
    ax3.set(xlabel="Date", ylabel="Volatility", title="IV history with regime bands")
    ax3.legend(fontsize=8, ncol=2)
    ax3.tick_params(axis="x", rotation=45)

    fig.suptitle("Synthetic demo — mean-reverting volatility (no IB connection)", fontsize=15)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(OUT, dpi=130, bbox_inches="tight")
    print("Saved:", OUT)
    print(f"Forward fit  : slope={ff.slope:.3f}  R^2={ff.rvalue ** 2:.3f}  (slope<1 => mean reversion)")
    print(f"Regime split : current vol = {split:.3f}")
    print(f"Current IV    : {iv.iloc[-1] * 100:.1f}%   IV-HV spread: {(iv.iloc[-1] - hv.iloc[-1]) * 100:.1f}%")


if __name__ == "__main__":
    main()
