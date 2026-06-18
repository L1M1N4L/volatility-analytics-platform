"""
Implied / Historical Volatility Trading Dashboard — Streamlit edition.

Pulls daily OPTION_IMPLIED_VOLATILITY and/or HISTORICAL_VOLATILITY bars from
Interactive Brokers (TWS / IB Gateway), then analyses the volatility regime,
its forward / mean-reversion behaviour, and the implied-vs-realized spread
(the volatility risk premium).

Run:
    streamlit run iv_dashboard.py

Requires a running TWS or IB Gateway with the API enabled (default paper port 7497).

Note on annualization
---------------------
IB's OPTION_IMPLIED_VOLATILITY and HISTORICAL_VOLATILITY are already *annualized*
decimals (0.18 == 18%). The original Tkinter version multiplied by sqrt(252),
which double-annualizes into nonsensical 300%+ readings. That factor is OFF by
default here; the sidebar toggle reinstates the legacy behaviour if you want it.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from scipy import stats

from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.wrapper import EWrapper

# Informational IB codes ("market data farm connection is OK") — not real errors.
_IB_INFO_CODES = {2104, 2106, 2158, 2107, 2119}

# Display label + IB whatToShow keyword for each series we can request.
SERIES_CHOICES: dict[str, tuple[str, str]] = {
    "Implied volatility": ("implied_vol", "OPTION_IMPLIED_VOLATILITY"),
    "Historical volatility": ("historical_vol", "HISTORICAL_VOLATILITY"),
}
VOL_LABELS = {"implied_vol": "Implied vol", "historical_vol": "Historical vol"}


# ---------------------------------------------------------------------------
# Interactive Brokers client
# ---------------------------------------------------------------------------
class IBApp(EWrapper, EClient):
    """IB wrapper that collects one historical request at a time and signals
    completion via threading.Events so the Streamlit thread can block on it."""

    def __init__(self) -> None:
        EClient.__init__(self, self)
        self.historical_data: dict[int, list[dict]] = {}
        self.errors: list[str] = []
        self._connected = threading.Event()
        self._data_end = threading.Event()

    # -- callbacks --------------------------------------------------------
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        if errorCode == 2176 and "fractional share" in errorString.lower():
            return  # noisy fractional-share rule warning
        if errorCode in _IB_INFO_CODES:
            return
        self.errors.append(f"[{errorCode}] {errorString}")

    def nextValidId(self, orderId):
        self._connected.set()

    def historicalData(self, reqId, bar):
        self.historical_data.setdefault(reqId, []).append(
            {"date": bar.date, "open": bar.open, "high": bar.high,
             "low": bar.low, "close": bar.close, "volume": bar.volume}
        )

    def historicalDataEnd(self, reqId, start, end):
        self._data_end.set()

    # -- helpers ----------------------------------------------------------
    def ready(self) -> bool:
        return self._connected.is_set()

    def request_history(self, contract: Contract, duration: str, what_to_show: str,
                        timeout: float = 15.0) -> list[dict]:
        """Request daily bars for one whatToShow; return as soon as data-end OR a
        request error arrives (e.g. bad duration) instead of waiting out the timeout."""
        self.historical_data.clear()
        self._data_end.clear()
        self.errors.clear()
        self.reqHistoricalData(
            reqId=1, contract=contract, endDateTime="", durationStr=duration,
            barSizeSetting="1 day", whatToShow=what_to_show, useRTH=1,
            formatDate=1, keepUpToDate=False, chartOptions=[],
        )
        deadline = time.time() + timeout
        while time.time() < deadline and not self._data_end.is_set() and not self.errors:
            time.sleep(0.1)
        return self.historical_data.get(1, [])


# ---------------------------------------------------------------------------
# Pure data / analysis helpers (no Streamlit, no IB state — easy to test)
# ---------------------------------------------------------------------------
def equity_contract(symbol: str) -> Contract:
    c = Contract()
    c.symbol = symbol.upper()
    c.secType = "STK"
    c.exchange = "SMART"
    c.currency = "USD"
    return c


def normalize_duration(s: str) -> str | None:
    """IB needs 'integer SPACE unit' (S/D/W/M/Y). Accept '3Y', '3 y', '30d', etc."""
    m = re.match(r"^\s*(\d+)\s*([SDWMY])\s*$", (s or "").strip().upper())
    return f"{m.group(1)} {m.group(2)}" if m else None


def build_vol_series(rows: list[dict], annualize: bool = False, periods: int = 252) -> pd.Series:
    """Date-indexed vol series from raw IB bars (the 'close' field carries vol)."""
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], format="mixed", errors="coerce")
    df = df.dropna(subset=["date"]).set_index("date").sort_index()
    s = df["close"].astype(float)
    return s * np.sqrt(periods) if annualize else s


def rolling_percentile(s: pd.Series, window: int = 252) -> pd.Series:
    return s.rolling(window=window, min_periods=20).rank(pct=True)


def classify_regime(pct: float) -> tuple[str, str]:
    if pd.isna(pct):
        return "N/A", "gray"
    if pct > 0.80:
        return "HIGH VOLATILITY", "red"
    if pct > 0.60:
        return "ABOVE AVERAGE", "orange"
    if pct > 0.40:
        return "NORMAL", "gray"
    if pct > 0.20:
        return "BELOW AVERAGE", "blue"
    return "LOW VOLATILITY", "green"


def reversion_signal(pct: float) -> tuple[str, str]:
    if pd.isna(pct):
        return "N/A", "gray"
    if pct > 0.80:
        return "EXPECT MEAN REVERSION DOWN", "blue"
    if pct < 0.20:
        return "EXPECT MEAN REVERSION UP", "red"
    return "NEUTRAL", "gray"


def analyze(vol: pd.Series) -> dict | None:
    """Regress 30-day forward vol on current vol and split into high/low regimes."""
    vol = vol.dropna()
    forward = vol.rolling(window=30, min_periods=1).mean().shift(-30)
    df = pd.DataFrame(
        {"current_vol": vol, "forward_30d_vol": forward, "vol_diff": forward - vol}
    ).dropna()

    if len(df) < 30:
        return None

    fit_forward = stats.linregress(df["current_vol"], df["forward_30d_vol"])
    fit_diff = stats.linregress(df["current_vol"], df["vol_diff"])

    # Regime boundary = fixed point where forward vol equals current vol.
    split = (
        fit_forward.intercept / (1 - fit_forward.slope)
        if fit_forward.slope != 1 else df["current_vol"].median()
    )
    high = df["current_vol"] > split
    low = ~high
    fit_high = stats.linregress(df.loc[high, "current_vol"], df.loc[high, "vol_diff"]) if high.sum() > 10 else None
    fit_low = stats.linregress(df.loc[low, "current_vol"], df.loc[low, "vol_diff"]) if low.sum() > 10 else None

    return {"df": df, "fit_forward": fit_forward, "fit_diff": fit_diff, "split": split,
            "high": high, "low": low, "fit_high": fit_high, "fit_low": fit_low}


def build_export(frame: pd.DataFrame, analysis_col: str, res: dict | None) -> pd.DataFrame:
    """Enriched frame for CSV download: vol series + percentile + forward/diff."""
    out = frame.copy()
    out[f"{analysis_col}_percentile"] = rolling_percentile(frame[analysis_col])
    if res is not None:
        out = out.join(res["df"][["forward_30d_vol", "vol_diff"]])
    out.index.name = "date"
    return out


# ---------------------------------------------------------------------------
# Plotly figures
# ---------------------------------------------------------------------------
def _arr(a) -> list:
    """Force plain Python lists for Plotly. Plotly 6 base64-encodes numpy/pandas
    arrays ('typed arrays'); older Streamlit's bundled Plotly.js can't decode them
    and silently drops the trace — so charts show axes but no points/lines."""
    return np.asarray(a).tolist()


def fig_forward(res: dict, label: str) -> go.Figure:
    df, fit = res["df"], res["fit_forward"]
    name = label.lower()
    fig = go.Figure()
    fig.add_scatter(x=_arr(df["current_vol"]), y=_arr(df["forward_30d_vol"]), mode="markers",
                    name="observations", marker=dict(size=5, opacity=0.45))
    xr = np.linspace(df["current_vol"].min(), df["current_vol"].max(), 100)
    fig.add_scatter(x=_arr(xr), y=_arr(fit.slope * xr + fit.intercept), mode="lines",
                    name=f"fit · R²={fit.rvalue ** 2:.3f}", line=dict(color="crimson", width=2))
    lo = min(df["current_vol"].min(), df["forward_30d_vol"].min())
    hi = max(df["current_vol"].max(), df["forward_30d_vol"].max())
    fig.add_scatter(x=[lo, hi], y=[lo, hi], mode="lines", name="y = x (no change)",
                    line=dict(color="black", dash="dash", width=1))
    fig.update_layout(title=f"Forward vs current — y = {fit.slope:.3f}x + {fit.intercept:.3f}",
                      xaxis_title=f"Current {name}", yaxis_title=f"30-day forward {name}",
                      height=430, margin=dict(t=50, b=40), legend=dict(orientation="h", y=-0.2))
    return fig


def fig_regime(res: dict, label: str) -> go.Figure:
    df, high, low = res["df"], res["high"], res["low"]
    name = label.lower()
    fig = go.Figure()
    fig.add_scatter(x=_arr(df.loc[high, "current_vol"]), y=_arr(df.loc[high, "vol_diff"]), mode="markers",
                    name="high-vol regime", marker=dict(color="crimson", size=5, opacity=0.45))
    fig.add_scatter(x=_arr(df.loc[low, "current_vol"]), y=_arr(df.loc[low, "vol_diff"]), mode="markers",
                    name="low-vol regime", marker=dict(color="royalblue", size=5, opacity=0.45))
    for mask, fit, color in ((high, res["fit_high"], "crimson"), (low, res["fit_low"], "royalblue")):
        if fit is None:
            continue
        x = df.loc[mask, "current_vol"]
        xr = np.linspace(x.min(), x.max(), 100)
        fig.add_scatter(x=_arr(xr), y=_arr(fit.slope * xr + fit.intercept), mode="lines",
                        line=dict(color=color, width=2), name=f"fit · R²={fit.rvalue ** 2:.3f}")
    fig.add_hline(y=0, line=dict(color="black", dash="dash", width=1))
    fig.add_vline(x=res["split"], line=dict(color="green", dash="dot", width=1),
                  annotation_text=f"split = {res['split']:.3f}", annotation_position="top")
    fig.update_layout(title="Forward − current vs current (regime split)",
                      xaxis_title=f"Current {name}", yaxis_title=f"Forward − current {name}",
                      height=430, margin=dict(t=50, b=40), legend=dict(orientation="h", y=-0.2))
    return fig


def fig_timeseries(frame: pd.DataFrame, symbol: str, analysis_col: str) -> go.Figure:
    fig = go.Figure()
    for col in frame.columns:
        fig.add_scatter(x=_arr(frame.index), y=_arr(frame[col]), mode="lines",
                        name=VOL_LABELS.get(col, col), line=dict(width=1.3))
    vol = frame[analysis_col].dropna()
    fig.add_hline(y=vol.quantile(0.75), line=dict(color="red", dash="dash"), annotation_text="75th pct")
    fig.add_hline(y=vol.quantile(0.25), line=dict(color="green", dash="dash"), annotation_text="25th pct")
    fig.add_hline(y=vol.mean(), line=dict(color="gray"), annotation_text="mean")
    fig.add_scatter(x=_arr([vol.index[-1]]), y=_arr([vol.iloc[-1]]), mode="markers", name="current",
                    marker=dict(color="crimson", size=12, line=dict(color="white", width=1)))
    fig.update_layout(title=f"{symbol} volatility — history with regime bands",
                      xaxis_title="Date", yaxis_title="Volatility",
                      height=430, margin=dict(t=50, b=40), legend=dict(orientation="h", y=-0.2))
    return fig


def fig_spread(frame: pd.DataFrame) -> go.Figure:
    spread = (frame["implied_vol"] - frame["historical_vol"]).dropna()
    fig = go.Figure()
    fig.add_scatter(x=_arr(spread.index), y=_arr(spread), mode="lines", name="implied − historical",
                    line=dict(width=1.3, color="purple"))
    fig.add_hline(y=0, line=dict(color="black", dash="dash"))
    fig.add_hline(y=spread.mean(), line=dict(color="gray", dash="dot"), annotation_text="mean")
    fig.update_layout(title="Volatility risk premium (implied − historical)",
                      xaxis_title="Date", yaxis_title="Spread", height=380,
                      margin=dict(t=50, b=40), legend=dict(orientation="h", y=-0.2))
    return fig


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------
def log(msg: str) -> None:
    st.session_state.log.append(f"[{datetime.now():%H:%M:%S}] {msg}")


def init_state() -> None:
    ss = st.session_state
    ss.setdefault("app", None)
    ss.setdefault("connected", False)
    ss.setdefault("frame", None)          # combined vol DataFrame
    ss.setdefault("analysis_col", "implied_vol")
    ss.setdefault("symbol", "SPY")
    ss.setdefault("log", [])


def do_connect(host: str, port: int, client_id: int) -> None:
    # Drop any previous connection first so we never leak its client id.
    if st.session_state.app is not None:
        try:
            st.session_state.app.disconnect()
        except Exception:
            pass
        st.session_state.app = None
        st.session_state.connected = False

    app = IBApp()
    try:
        app.connect(host, int(port), clientId=int(client_id))
        threading.Thread(target=app.run, daemon=True).start()

        # Wait for the API handshake (nextValidId) — but bail early on a fatal error.
        deadline = time.time() + 8
        while time.time() < deadline and not app.ready() and not app.errors:
            time.sleep(0.1)

        if app.ready():
            st.session_state.app = app
            st.session_state.connected = True
            log(f"Connected to IB at {host}:{port} (clientId={client_id})")
        else:
            reason = "; ".join(app.errors) if app.errors else "no response within 8s"
            log(f"Failed to connect (clientId={client_id}): {reason}")
            if any("326" in e for e in app.errors):
                log("→ Client ID already in use. Increase 'Client ID' in the sidebar and retry.")
            try:
                app.disconnect()
            except Exception:
                pass
    except Exception as exc:  # noqa: BLE001
        log(f"Connection error: {exc}")


def do_disconnect() -> None:
    app = st.session_state.app
    if app is not None:
        try:
            app.disconnect()
        except Exception as exc:  # noqa: BLE001
            log(f"Disconnect error: {exc}")
    st.session_state.app = None
    st.session_state.connected = False
    st.session_state.frame = None
    log("Disconnected from IB")


def do_query(symbol: str, duration: str, annualize: bool, chosen: list[str]) -> None:
    app = st.session_state.app
    if app is None:
        log("Not connected.")
        return

    dur = normalize_duration(duration)
    if dur is None:
        log(f"Invalid duration '{duration}' — use 'integer SPACE unit', e.g. '2 Y', '6 M', '30 D'.")
        return

    contract = equity_contract(symbol)
    series: dict[str, pd.Series] = {}
    for label in chosen:
        col, what_to_show = SERIES_CHOICES[label]
        log(f"Querying {label} for {symbol} ({dur})…")
        rows = app.request_history(contract, dur, what_to_show)
        if rows:
            series[col] = build_vol_series(rows, annualize=annualize)
            log(f"  received {len(rows)} points")
        else:
            why = "; ".join(app.errors) if app.errors else "no data / timeout"
            log(f"  no {label} data: {why}")

    if not series:
        st.session_state.frame = None
        log("No usable data received.")
        return

    frame = pd.concat(series, axis=1).sort_index()
    st.session_state.frame = frame
    st.session_state.symbol = symbol.upper()
    st.session_state.analysis_col = "implied_vol" if "implied_vol" in frame else frame.columns[0]
    log(f"Built frame: {len(frame)} rows · {frame.index.min():%Y-%m-%d} → {frame.index.max():%Y-%m-%d}")


def sidebar() -> None:
    ss = st.session_state
    with st.sidebar:
        st.subheader("Interactive Brokers")
        host = st.text_input("Host", "127.0.0.1")
        c1, c2 = st.columns(2)
        port = c1.number_input("Port", value=7497, step=1, format="%d")
        client_id = c2.number_input(
            "Client ID", value=1, step=1, format="%d",
            help="Unique per API connection. Bump this if you see 'client id already in use'.")

        col_c, col_d = st.columns(2)
        if col_c.button("Connect", disabled=ss.connected, use_container_width=True):
            do_connect(host, port, client_id)
            st.rerun()
        if col_d.button("Disconnect", disabled=not ss.connected, use_container_width=True):
            do_disconnect()
            st.rerun()
        st.markdown(":green[● connected]" if ss.connected else ":red[● disconnected]")

        st.divider()
        st.subheader("Query")
        symbol = st.text_input("Symbol", ss.symbol)
        duration = st.text_input("Duration", "2 Y",
                                  help="Number + unit S/D/W/M/Y, e.g. '2 Y', '6 M', '30 D'. "
                                       "'3Y' is auto-fixed to '3 Y'.")
        chosen = st.multiselect("Series", list(SERIES_CHOICES), default=["Implied volatility"])
        annualize = st.toggle("Apply √252 factor", value=False,
                              help="Leave off — IB already returns annualized vol. On = legacy behaviour.")

        ready = ss.connected and bool(chosen)
        if st.button("Query data", disabled=not ready, type="primary", use_container_width=True):
            with st.spinner(f"Requesting {symbol}…"):
                do_query(symbol, duration, annualize, chosen)
            st.rerun()
        if not chosen:
            st.caption("Pick at least one series.")

        # Choose which series drives the regime/forward analysis when two are loaded.
        frame = ss.frame
        if frame is not None and frame.shape[1] > 1:
            cols = list(frame.columns)
            if ss.analysis_col not in cols:
                ss.analysis_col = cols[0]
            ss.analysis_col = st.radio("Analyze", cols, index=cols.index(ss.analysis_col),
                                       format_func=lambda c: VOL_LABELS.get(c, c))


def render_metrics(frame: pd.DataFrame, analysis_col: str) -> None:
    vol = frame[analysis_col].dropna()
    current = vol.iloc[-1]
    pct = rolling_percentile(vol).iloc[-1]
    regime_label, regime_color = classify_regime(pct)
    rev_label, rev_color = reversion_signal(pct)

    st.caption(f"Analyzing **{VOL_LABELS.get(analysis_col, analysis_col)}**")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Current", f"{current * 100:.2f}%")
    c2.metric("Percentile (1y)", "N/A" if pd.isna(pct) else f"{pct * 100:.1f}%")
    c3.metric("Range (min–max)", f"{vol.min() * 100:.1f}–{vol.max() * 100:.1f}%")
    c4.metric("Mean", f"{vol.mean() * 100:.2f}%")
    st.markdown(
        f"**Regime:** :{regime_color}[{regime_label}]  ·  "
        f"**Mean-reversion signal:** :{rev_color}[{rev_label}]"
    )


def render_spread(frame: pd.DataFrame) -> None:
    spread = (frame["implied_vol"] - frame["historical_vol"]).dropna()
    if spread.empty:
        return
    with st.expander("Implied vs realized — volatility risk premium", expanded=False):
        c1, c2 = st.columns(2)
        c1.metric("Current spread", f"{spread.iloc[-1] * 100:.2f}%")
        c2.metric("Mean spread", f"{spread.mean() * 100:.2f}%")
        st.plotly_chart(fig_spread(frame), use_container_width=True)
        st.caption("Positive = options price in more vol than recently realized (premium to sellers).")


def render_analysis(res: dict, frame: pd.DataFrame, symbol: str, analysis_col: str) -> None:
    label = VOL_LABELS.get(analysis_col, analysis_col)
    left, right = st.columns(2)
    left.plotly_chart(fig_forward(res, label), use_container_width=True)
    right.plotly_chart(fig_regime(res, label), use_container_width=True)
    st.plotly_chart(fig_timeseries(frame, symbol, analysis_col), use_container_width=True)

    ff, fd = res["fit_forward"], res["fit_diff"]
    st.info(
        "Forward volatility **mean-reverts** (slope < 1)." if ff.slope < 1
        else "Forward volatility **trends** (slope > 1).", icon="📈")
    st.info(
        "High current vol predicts **lower** future vol — mean reversion." if fd.slope < 0
        else "High current vol predicts **higher** future vol — momentum.", icon="📊")

    with st.expander("Regression detail"):
        rows = [
            ("Forward ~ current", ff.slope, ff.intercept, ff.rvalue ** 2, ff.pvalue, len(res["df"])),
            ("Vol diff ~ current", fd.slope, fd.intercept, fd.rvalue ** 2, fd.pvalue, len(res["df"])),
        ]
        for name, fit, mask in (("High-vol regime", res["fit_high"], res["high"]),
                                ("Low-vol regime", res["fit_low"], res["low"])):
            if fit is not None:
                rows.append((name, fit.slope, fit.intercept, fit.rvalue ** 2, fit.pvalue, int(mask.sum())))
        table = pd.DataFrame(rows, columns=["model", "slope", "intercept", "R²", "p-value", "n"])
        st.dataframe(
            table.style.format({"slope": "{:.4f}", "intercept": "{:.4f}",
                                "R²": "{:.4f}", "p-value": "{:.4g}"}),
            use_container_width=True, hide_index=True)
        st.caption(f"Regime split at current vol = {res['split']:.4f}.")
        st.download_button("⬇ Regression table (CSV)", table.to_csv(index=False).encode("utf-8"),
                           file_name=f"{symbol}_regressions.csv", mime="text/csv", key="dl_reg")


def main() -> None:
    st.set_page_config(page_title="Volatility Dashboard", page_icon="📈", layout="wide")
    init_state()
    st.title("📈 Implied & Historical Volatility Dashboard")
    sidebar()

    frame = st.session_state.frame
    if frame is None or frame.empty:
        st.info("Connect to IB, choose your series, then **Query data**. "
                "(TWS / IB Gateway must be running with the API enabled.)")
    else:
        symbol, analysis_col = st.session_state.symbol, st.session_state.analysis_col
        res = analyze(frame[analysis_col])

        render_metrics(frame, analysis_col)
        export = build_export(frame, analysis_col, res)
        st.download_button("⬇ Download data (CSV)", export.to_csv().encode("utf-8"),
                           file_name=f"{symbol}_volatility.csv", mime="text/csv", key="dl_data")
        if {"implied_vol", "historical_vol"}.issubset(frame.columns):
            render_spread(frame)

        st.divider()
        if res is None:
            st.warning("Not enough history for the regime/forward analysis (need ~60+ points).")
        else:
            render_analysis(res, frame, symbol, analysis_col)

    if st.session_state.app is not None and st.session_state.app.errors:
        with st.expander(f"IB messages ({len(st.session_state.app.errors)})"):
            st.code("\n".join(st.session_state.app.errors[-50:]))
    with st.expander("Activity log"):
        st.code("\n".join(st.session_state.log[-100:]) or "—")


if __name__ == "__main__":
    main()
