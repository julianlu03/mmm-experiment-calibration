# MMM vs. Experiment: Calibrating a Marketing Mix Model with a Geo Holdout

**Status:** Day 1 of 5 complete (simulator, raw exports, BigQuery load).

Marketing mix models can be confidently wrong, and with real data you never find out,
because the true channel ROIs are unknown. This project builds a simulated DTC e-commerce
brand where the truth is known, plants two realistic failure modes, and tests whether a
geo holdout experiment can correct a Meridian MMM that gets paid search wrong.

**Pipeline:** Python simulator → messy platform exports → BigQuery → dbt → Meridian (uncalibrated)
→ DiD and synthetic control on a geo holdout → Meridian (calibrated with the experiment)
→ compare everything to the truth → Tableau.

## Why simulated data

This is a parameter recovery study: before trusting a model's ROIs, check whether it
recovers effects you planted. Tool builders do this routinely (Google's AMSS simulator,
Meridian's and PyMC-Marketing's demo data). The simulation is the only setting where
"how far off was the MMM?" has an answer.

## The simulated world

A DTC brand selling across the 50 US states (2020 Census populations), 156 weeks from
2023-01-02 to 2025-12-28, about $650M in annual net sales. KPI is weekly net sales by state.

| Channel | True ROI | Adstock decay | Share of revenue | Role in the story |
|---|---|---|---|---|
| Paid search | 1.80 | 0.15 (fast) | 9.5% | Spend follows hidden demand, so a naive MMM overstates it |
| Paid social | 1.40 | 0.40 | 4.8% | Exogenous; the MMM should recover it |
| Online video | 0.80 | 0.65 (slow) | 1.5% | Below breakeven; runs in regional flights |

Baseline demand (84% of revenue) includes seasonality with a December peak, seven
promo events a year, state-level growth trends, and hidden state-level demand shocks.
All parameters live in [`config.py`](config.py); the modeling code never imports it.

## The two failure modes

**1. Endogeneity (paid search).** Search spend rises when people are already searching, so
it moves with demand the model can't see. In the simulator, a hidden AR(1) demand shock
raises baseline sales and raises search spend (elasticity 1.25). The model sees "search
spend up, sales up" and credits search for demand that would have converted anyway.
This can't be spotted in a raw correlation table: search spend and the hidden shock
correlate at 0.02 in levels.

**2. Non-parallel trends (geo experiment).** Paid search is paused for 8 weeks
(2024-06-03 to 2024-07-28) in 10 states picked from a "growth markets" list: mid-sized
states with the fastest trends. Their faster growth violates DiD's parallel-trends
assumption, so DiD understates the loss from pausing search. Other fast-growing states
remain in the donor pool, so synthetic control can still build a matching counterfactual.

The test sits mid-sample on purpose. An MMM's state fixed effects absorb each state's
average level; a pause in fast-growing states near the end of the sample would line up
zero spend with above-average sales and bias the MMM too, mixing the two failure modes.

**Also planted: platform attribution.** Google Ads and Meta report conversion value at
1.6x / 1.3x / 1.1x true incremental value, and only in weeks a channel is live, so
slow-decay video gets under-credited.

## Does the simulator do what it claims?

[`validate_simulation.py`](validate_simulation.py) checks each failure mode on the default
seed and across 20 seeds (full output in [`validation_report.md`](validation_report.md)).

| Check | Truth | Result (seed 42) | Across 20 seeds |
|---|---|---|---|
| Search ROI, oracle regression,* search follows demand | 1.80 | 2.74 | mean 2.81 |
| Search ROI, same regression, search exogenous | 1.80 | 1.83 | mean 1.82 |
| Experiment iROAS, DiD (26-week pre-period) | 1.77 | 1.44 | mean error -0.29 |
| Experiment iROAS, DiD (52-week pre-period) | 1.77 | 1.29 | mean error -0.50 |
| Experiment iROAS, synthetic control | 1.77 | 1.75 | mean error -0.12 |
| Platform-reported ROAS: search / social / video | 1.80 / 1.40 / 0.80 | 2.88 / 1.82 / 0.66 | |

\*Per-capita sales on state and week fixed effects plus the **true** adstock and saturation
transforms. A best case for MMM, not Meridian. If even this is biased by ~50%, the
problem is the data-generating process, not the model's flexibility.

Two things worth noting already: the bias disappears entirely when search spend stops
following demand (so endogeneity is the cause), and a longer DiD pre-period makes the
trend bias worse, not better.

## Raw exports (what the analyst receives)

| File | Grain | Rows | Notes |
|---|---|---|---|
| `google_ads_geo_daily.csv` | day x campaign x state | 144,886 | Search (brand, non-brand) and YouTube; Fivetran-style columns |
| `meta_ads_region_daily.csv` | day x campaign x state | 109,200 | Prospecting and retargeting; Ads Manager headers |
| `shopify_sales_daily.csv` | day x state | 55,692 | Gross sales, discounts, returns, shipping, taxes |
| `promo_calendar.csv` | promo | 21 | Marketing's Google Sheet |
| `geo_experiment_design.csv` | state | 50 | Test/control assignment and dates |
| `census_state_population.csv` | state | 50 | Population for per-capita scaling |

The exports carry realistic quirks for dbt to fix (duplicates, unit and sign conventions,
geo naming, missing zero-spend rows, week boundaries). They are listed in
[`docs/known_data_issues.md`](docs/known_data_issues.md), which is a spoiler: profile the
raw tables first.

Ground truth goes to `data/truth/` and the `mmm_truth` BigQuery dataset, used only for
evaluation.

## Running it

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python simulate.py              # writes data/raw and data/truth (seed 42, ~1 second)
python validate_simulation.py   # writes validation_report.md (~10 seconds)

gcloud auth application-default login
python load_to_bigquery.py --project YOUR_GCP_PROJECT --dry-run
python load_to_bigquery.py --project YOUR_GCP_PROJECT
```

BigQuery sandbox notes: tables expire after 60 days (rerun the loader, everything is
seeded), and DML isn't supported, so dbt models should be `table` or `view`, not
`incremental`.

## Repo layout

```
config.py                  ground-truth parameters (evaluation only)
simulate.py                true world + messy raw exports
validate_simulation.py     checks the failure modes exist
load_to_bigquery.py        lands raw + truth tables in BigQuery
validation_report.md       output of the validation script
docs/known_data_issues.md  planted data quality issues (spoilers)
```

## Roadmap

- **Day 2:** dbt project: staging, intermediate, and a state x week mart, with tests and docs
- **Day 3:** Meridian v1 (uncalibrated): convergence checks, ROI recovery vs. truth
- **Day 4:** DiD and synthetic control on the holdout; Meridian v2 with the experiment as a search ROI prior
- **Day 5:** Tableau (ROI recovery, experiment lift, budget reallocation) and the write-up
