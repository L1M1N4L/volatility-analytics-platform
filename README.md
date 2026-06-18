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

## Background: the concepts

If you're new to volatility trading, this section explains what the dashboard is actually measuring and why it matters. If you already know this, skip to [Analysis methodology](#analysis-methodology).

### What "volatility" means here

Volatility is the standard deviation of an asset's returns, quoted in **annualized** terms. A stock with 20% volatility is expected (one standard deviation, ~68% of the time) to finish the year within ±20% of where it started. It's a measure of uncertainty, not direction — vol is high whether the market is crashing or melting up.

There are two flavors, and the gap between them is the whole game:

- **Historical (realized) volatility (HV)** — how much the stock *actually* moved, computed from past price returns. It's backward-looking and a matter of record.
- **Implied volatility (IV)** — the volatility *implied by option prices*. Because an option's price rises with expected future movement, you can invert an option-pricing model (Black–Scholes) to back out the volatility the market is pricing in. It's forward-looking and a matter of opinion — it's the market's collective forecast of future realized vol.

> **The core insight:** IV is a *forecast*; HV is the *outcome*. Comparing them tells you whether the market's fear was justified.

### Volatility risk premium (IV − HV)

On average, **implied vol trades richer than the realized vol that follows**. Over the long run IV ≈ HV + a few points. This persistent gap is the **volatility risk premium (VRP)**.

Why does it exist? Options are insurance. Most people are net buyers of protection (puts to hedge crashes), and they'll overpay for it the same way you overpay for home insurance — you don't expect your house to burn down, but you pay the premium anyway. The sellers of that insurance (vol sellers) collect the premium as compensation for taking on tail risk. The VRP is the option seller's "edge," and it's why strategies like covered calls and short straddles have a positive expected return — they're harvesting insurance premium, with the occasional painful payout when realized vol spikes past what was priced.

- **VRP > 0** (the usual state): options are expensive relative to what unfolds → favors *selling* vol.
- **VRP < 0** (rare, during crises): realized vol is outrunning what options priced → favors *buying* vol, and a warning sign that the market is underpricing risk.

### Mean reversion

Volatility is one of the most reliably **mean-reverting** quantities in finance. Unlike prices (which trend and are roughly a random walk), vol gets pulled back toward a long-run average:

- After a shock (earnings, a crash, a Fed surprise), vol **spikes** — then decays back down as the panic fades.
- During long calm stretches, vol **drifts to lows** — then eventually gets jolted back up.

This is why "is vol high or low *relative to its own history*?" is a tradable question. If vol is in the 95th percentile, the base rate strongly favors it falling rather than rising further. This dashboard quantifies that tendency two ways: a **percentile rank** (where are we now?) and a **forward regression** (how strongly does vol revert from here?).

### Volatility regimes

Vol doesn't revert to the same level at the same speed all the time. Markets cluster into **regimes** — calm, low-vol periods (think 2017) versus turbulent, high-vol periods (2008, March 2020). Inside a high-vol regime, mean reversion is fast and violent (spikes collapse quickly); in a low-vol regime, vol can grind sideways for a long time. The dashboard detects the boundary between these regimes statistically and measures reversion strength separately on each side, because a single average would blur two very different behaviors together.

---

## Analysis methodology

This section maps the concepts above onto the exact computations the code performs (see [`iv_dashboard.py`](iv_dashboard.py)).

### Vol percentile and regime label

A **rolling 252-day percentile rank** (252 ≈ trading days per year) of current vol against its own trailing-year history. If today's vol exceeds 95% of the last year's readings, the rank is 0.95.

This single number drives the metrics row:

| Percentile | Regime label | Mean-reversion signal |
|---|---|---|
| > 80% | HIGH VOLATILITY | expect reversion **down** |
| 60–80% | ABOVE AVERAGE | neutral |
| 40–60% | NORMAL | neutral |
| 20–40% | BELOW AVERAGE | neutral |
| < 20% | LOW VOLATILITY | expect reversion **up** |

The logic: extreme percentiles are unsustainable, so the edges flag the directions in which reversion is most likely.

### Forward volatility regression

The direct test of mean reversion. For each day, compute the **average vol over the next 30 days** (`forward_30d_vol`) and regress it on today's vol:

```
forward_vol = slope × current_vol + intercept
```

Read the **slope**:

- **slope < 1** → mean reversion. A unit of extra vol today translates into *less* than a unit of extra vol over the next month — high vol decays, low vol recovers. This is the normal, expected result.
- **slope ≈ 1** → a random walk; today's vol is the best guess for tomorrow's (no reversion).
- **slope > 1** → momentum/trending; vol begets more vol (unusual, seen during regime breaks).

The **fixed point** — where the regression line crosses `y = x`, i.e. `intercept / (1 − slope)` — is the level at which vol predicts *no change*. It's the implied long-run equilibrium vol, and the code uses it as the boundary between the two regimes. Above it, vol tends to fall back; below it, vol tends to rise toward it.

### Regime split

To capture that reversion behaves differently in calm vs. turbulent markets, observations are split at the fixed point into **high-vol** and **low-vol** regimes, and each gets its own OLS fit on the **vol difference** (`forward − current`):

- A **negative slope** on `forward − current` means high current vol predicts a negative change (reversion down) — the steeper the slope, the stronger and faster the reversion.
- Comparing the high-regime slope to the low-regime slope shows the asymmetry: typically the high-vol regime reverts much harder (spikes collapse) than the low-vol regime (calm persists).

The dashboard reports slope, intercept, R² (how much of the variation the relationship explains), p-value (statistical significance), and sample size for each fit, downloadable as CSV.

### Volatility risk premium

When both series are loaded, the dashboard plots the **IV − HV spread** over time with its historical mean. Positive readings (the usual case) show options pricing in more vol than was recently realized — the premium accruing to vol sellers. Watch for the spread going negative or spiking: that's the market repricing risk faster than realized vol has caught up.

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
