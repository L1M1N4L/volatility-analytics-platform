# Volatility Analytics Platform

A Streamlit dashboard for analyzing implied and historical volatility of equities using live data from Interactive Brokers. Includes regime classification, forward vol regression, mean-reversion signals, and volatility risk premium tracking.

![Demo](demo_volatility_analysis.png)

---

## Features

- **Live IB data** — connects to TWS or IB Gateway via the IB API and pulls daily implied volatility (`OPTION_IMPLIED_VOLATILITY`) and historical volatility (`HISTORICAL_VOLATILITY`) bars
- **Regime classification** — rolling 1-year percentile rank with labeled regimes (Low / Below Average / Normal / Above Average / High)
- **Mean-reversion signal** — flags when current vol is extreme and likely to revert
- **Forward vol regression** — OLS of 30-day forward vol on current vol; slope < 1 confirms mean reversion
- **Regime split analysis** — separates high-vol and low-vol regimes at the regression fixed point and fits separate trendlines
- **Volatility risk premium** — implied minus historical vol spread over time
- **CSV export** — download the enriched vol series or the regression table

---

## Requirements

- Python 3.9+
- A running TWS or IB Gateway instance with the API enabled (default paper port: `7497`)
- IB API Python client (`ibapi`)

Install dependencies:

```bash
pip install -r requirements.txt
```

> **ibapi note:** The PyPI package (`ibapi>=9.81`) works for historical IV/HV requests. For IB's current 10.x client, install from the TWS API download:
> ```bash
> cd "C:\TWS API\source\pythonclient" && pip install .
> ```

---

## Usage

### Live dashboard

```bash
streamlit run iv_dashboard.py
```

1. In the sidebar, connect to IB (host `127.0.0.1`, port `7497` for paper trading)
2. Enter a symbol (e.g. `SPY`) and duration (e.g. `2 Y`)
3. Select one or both series (implied vol, historical vol)
4. Click **Query data**

### Offline demo (no IB connection)

Generates a static PNG using synthetic mean-reverting vol data to demonstrate the full analysis pipeline:

```bash
python demo_plot.py
```

Output: `demo_volatility_analysis.png`

---

## Analysis methodology

### Forward volatility regression

Regresses 30-day forward vol against current vol:

```
forward_vol = slope × current_vol + intercept
```

A slope < 1 indicates mean reversion. The fixed point (`intercept / (1 - slope)`) defines the regime boundary.

### Regime split

Observations above and below the fixed point are separated into high-vol and low-vol regimes. Each regime gets its own OLS fit on the vol difference (`forward − current`), which shows how strongly vol reverts in each regime.

### Vol percentile

Rolling 252-day rank (percentile) of current vol. Drives the regime label and mean-reversion signal shown in the metrics row.

### Volatility risk premium

`implied_vol − historical_vol` — when positive, options are pricing in more vol than recently realized, which is the typical condition (sellers collect a premium).

---

## Annualization note

IB's `OPTION_IMPLIED_VOLATILITY` and `HISTORICAL_VOLATILITY` are already annualized decimals (e.g. `0.18` = 18%). The `√252` toggle in the sidebar is off by default. Enabling it reinstates a legacy behavior that double-annualizes the series into incorrect 300%+ readings — leave it off unless you have a specific reason.

---

## Project structure

```
iv_dashboard.py          # Streamlit app — IB client, analysis functions, UI
demo_plot.py             # Offline demo using synthetic data
demo_volatility_analysis.png  # Output of demo_plot.py
requirements.txt         # Python dependencies
```
