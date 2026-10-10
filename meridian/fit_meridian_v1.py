"""
Meridian v1: uncalibrated geo-level MMM fit on mart_mmm__state_week.

Run from the project root with .venv-meridian active:
    python meridian/fit_meridian_v1.py

Steps:
    1. Load the dbt mart and build Meridian InputData
    2. Check the InputData totals against the raw DataFrame
    3. Define the ModelSpec (Meridian defaults, no controls)
    4. Sample the prior and the posterior (MCMC)
    5. Save the fitted model so diagnostics never need a refit
    6. Convergence (R-hat) and model fit diagnostics
    7. Save ROI posterior summaries to outputs/

Design rules:
    - This script never reads config.py or data/truth/. Comparing ROI to the
      truth happens in a separate evaluation script, so the model cannot
      "peek" at the answer.
    - promo_share is NOT passed as a control: it is national, so it is
      collinear with the weekly time effects (knots = n_times). The weekly
      time effects absorb promos and seasonality.
    - The experiment flags are NOT used in v1 (they are Day 4 material).
"""

import os

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"  # must run before Meridian/TensorFlow import

import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from meridian.analysis import analyzer
from meridian.data import data_frame_input_data_builder
from meridian.model import model, spec
from meridian.schema.serde import meridian_serde  # needs: pip install "google-meridian[schema]"

# Library-internal deprecation notices; nothing in this script can fix them.
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
DATA_PATH = Path("data/mart_mmm__state_week.csv")
OUTPUT_DIR = Path("outputs")
MODEL_PATH = OUTPUT_DIR / "meridian_v1_model.binpb"
ROI_PATH = OUTPUT_DIR / "meridian_v1_roi.csv"

CHANNELS = ["paid_search", "paid_social", "online_video"]
SPEND_COLS = [f"spend_{c}" for c in CHANNELS]
SEED = 42

# Set REFIT = False after a good fit to rerun diagnostics from the saved model.
REFIT = False

# MCMC settings. SMOKE checks the pipeline end to end and times it on this
# machine; FULL is the real fit.
MCMC_SMOKE = {"n_chains": 2, "n_adapt": 50, "n_burnin": 50, "n_keep": 50}
MCMC_FULL = {"n_chains": 4, "n_adapt": 2000, "n_burnin": 1000, "n_keep": 1000}
MCMC = MCMC_FULL
N_PRIOR_DRAWS = 500


# ---------------------------------------------------------------------------
# 1. Load the mart and build InputData
# ---------------------------------------------------------------------------
def load_mart(path: Path) -> pd.DataFrame:
    """Read the mart CSV as-is; the builder picks the columns it needs."""
    return pd.read_csv(path)


def build_input_data(df: pd.DataFrame):
    """Turn the long state-week table into Meridian InputData.

    - kpi_type="revenue": net_sales is in dollars, so ROI comes out as
      dollars per dollar, the same unit as the truth. No revenue_per_kpi needed.
    - Geo = state_code, time = week_start (Monday, "YYYY-MM-DD").
    - The mart has no impressions, so spend is used as both the media
      (exposure) input and the spend (ROI denominator) input.
    """
    builder = data_frame_input_data_builder.DataFrameInputDataBuilder(
        kpi_type="revenue",
        default_geo_column="state_code",
        default_time_column="week_start",
        default_population_column="population",
        default_kpi_column="net_sales",
    )
    data = (
        builder
        .with_kpi(df)
        .with_population(df)
        .with_media(
            df,
            media_cols=SPEND_COLS,
            media_spend_cols=SPEND_COLS,
            media_channels=CHANNELS,
        )
        .build()
    )
    return data


# ---------------------------------------------------------------------------
# 2. Sanity checks. Meridian will not catch a wrong load; this will.
# ---------------------------------------------------------------------------
def check_input_data(data, df: pd.DataFrame) -> None:
    n_geos = df["state_code"].nunique()
    n_weeks = df["week_start"].nunique()

    assert data.kpi.shape == (n_geos, n_weeks), data.kpi.shape
    assert data.population.shape == (n_geos,), data.population.shape
    assert data.media.shape == (n_geos, n_weeks, len(CHANNELS)), data.media.shape
    assert data.media_spend.shape == (n_geos, n_weeks, len(CHANNELS)), data.media_spend.shape

    kpi_diff = abs(float(data.kpi.sum()) - df["net_sales"].sum())
    assert kpi_diff < 0.01, f"KPI total off by {kpi_diff:,.2f}"

    # Select channels by name, never by position: Meridian may reorder them.
    spend_by_channel = data.media_spend.sum(dim=["geo", "time"]).to_series()
    for channel, col in zip(CHANNELS, SPEND_COLS):
        spend_diff = abs(spend_by_channel[channel] - df[col].sum())
        assert spend_diff < 0.01, f"{channel} spend off by {spend_diff:,.2f}"

    print(f"Input data OK: {n_geos} geos x {n_weeks} weeks x {len(CHANNELS)} channels")
    print(f"  KPI total: ${float(data.kpi.sum()) / 1e6:,.2f}M")
    for channel in CHANNELS:
        print(f"  {channel} spend: ${spend_by_channel[channel] / 1e6:,.2f}M")


# ---------------------------------------------------------------------------
# 3. Model specification
# ---------------------------------------------------------------------------
def build_model_spec():
    """v1 = Meridian defaults on purpose (the "naive analyst" baseline).

    Written out explicitly to document the choices and guard against
    default changes in future Meridian versions:
        - knots=None: one time effect per week (156) in a geo model; absorbs
          seasonality and the national promo calendar.
        - max_lag=8: up to 8 weeks of adstock carryover.
        - media_prior_type="roi": prior placed directly on each channel's ROI,
          default LogNormal(0.2, 0.9) (median 1.22, 90% interval 0.28 to 5.37).
          Day 4 calibration replaces the paid_search ROI prior.
    """
    return spec.ModelSpec(
        knots=None,
        max_lag=8,
        media_prior_type="roi",
    )


# ---------------------------------------------------------------------------
# 4. Fit
# ---------------------------------------------------------------------------
def fit_model(data, model_spec):
    """Create the Meridian model, sample the prior, then the posterior.

    The prior draws let us compare prior vs posterior ROI later, which shows
    how much the data (and on Day 4, the experiment) moved beliefs.
    """
    mmm = model.Meridian(input_data=data, model_spec=model_spec)
    mmm.sample_prior(n_draws=N_PRIOR_DRAWS, seed=SEED)
    mmm.sample_posterior(**MCMC, seed=SEED)  # ** unpacks n_chains, n_adapt, n_burnin, n_keep
    return mmm


# ---------------------------------------------------------------------------
# 5. Save / load the fitted model
# ---------------------------------------------------------------------------
def save_model(mmm, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    meridian_serde.save_meridian(mmm, str(path))
    print(f"Saved model to {path} ({path.stat().st_size / 1e6:.1f} MB)")


def load_model(path: Path):
    return meridian_serde.load_meridian(str(path))


# ---------------------------------------------------------------------------
# 6. Diagnostics
# ---------------------------------------------------------------------------
RHAT_THRESHOLD = 1.1  # Meridian's default flag is 1.2; 1.1 is a stricter, common rule of thumb


def report_convergence(mmm) -> bool:
    """R-hat per parameter group. R-hat compares variance between chains to
    variance within chains; ~1.0 means the chains agree on the same posterior.
    Returns True if every parameter is below RHAT_THRESHOLD.
    """
    rhat = analyzer.Analyzer(mmm).rhat_summary(bad_rhat_threshold=RHAT_THRESHOLD)
    rhat["n_params"] = rhat["n_params"].astype(int)
    cols = ["param", "n_params", "avg_r_hat", "max_r_hat", "percent_bad_r_hat"]

    print("\n=== Convergence (R-hat) ===")
    print(rhat[cols].round(3).to_string(index=False))

    failing = rhat.loc[rhat["max_r_hat"] >= RHAT_THRESHOLD, "param"].tolist()
    converged = not failing
    if converged:
        print(f"CONVERGED: every parameter has max R-hat < {RHAT_THRESHOLD}")
    else:
        print(f"NOT CONVERGED: max R-hat >= {RHAT_THRESHOLD} for {', '.join(failing)}")
    return converged


def report_chain_agreement(mmm) -> None:
    """Show WHERE the chains disagree, per chain.

    R-hat only says that chains disagree. This shows whether the disagreement
    reaches the numbers we care about (ROI by channel) or only the baseline
    level (how sales are split between weekly time effects mu_t and state
    intercepts tau_g: raise every week by c, lower every state by c, same fit).
    """
    post = mmm.inference_data.posterior

    roi = np.asarray(analyzer.Analyzer(mmm).roi())  # shape (chain, draw, channel)
    channels = list(mmm.input_data.media.coords["media_channel"].values)
    by_chain = pd.DataFrame(roi.mean(axis=1), columns=channels)  # mean over draws -> chain x channel
    by_chain["avg_mu_t"] = post["mu_t"].mean(dim=["draw", "time"]).values
    by_chain["avg_tau_g"] = post["tau_g"].mean(dim=["draw", "geo"]).values
    by_chain.index.name = "chain"

    print("\n=== Posterior means by chain ===")
    print(by_chain.round(3).to_string())
    print("ROI columns should match across chains. avg_mu_t and avg_tau_g are on")
    print("Meridian's scaled KPI, so compare them across chains, not to dollars.")


def report_fit(mmm) -> None:
    """In-sample fit of expected vs actual sales: R-squared, MAPE, wMAPE,
    at state level ("geo") and summed nationally ("national").

    Good fit is necessary but NOT sufficient: a model can fit sales well and
    still split credit between channels wrongly (that is the whole point of
    this project).
    """
    acc = analyzer.Analyzer(mmm).predictive_accuracy()
    table = acc["value"].to_pandas()  # rows = metric, cols = geo_granularity

    print("\n=== Model fit (in-sample) ===")
    print(table.round(4).to_string())


# ---------------------------------------------------------------------------
# 7. ROI summaries for Day 4 and Day 5
# ---------------------------------------------------------------------------
def save_roi_summary(mmm, path: Path, model_version: str = "v1") -> pd.DataFrame:
    """Prior and posterior ROI per channel to CSV (tidy: one row per
    channel x distribution), plus incremental sales, for the truth comparison
    and Tableau. model_version lets v1 and v2 rows stack in one file later.
    """
    metrics = analyzer.Analyzer(mmm).summary_metrics(confidence_level=0.9)

    roi = (
        metrics["roi"]
        .sel(channel=CHANNELS)  # drop the "All Channels" row
        .to_dataframe()["roi"]
        .unstack("metric")  # columns: mean, median, ci_lo, ci_hi
        .rename(columns=lambda m: f"roi_{m}")
    )
    inc = (
        metrics["incremental_outcome"]
        .sel(channel=CHANNELS, metric="mean")
        .to_dataframe()["incremental_outcome"]
        .rename("incremental_sales_mean")
    )
    spend = metrics["spend"].sel(channel=CHANNELS).to_series().rename("spend")

    summary = (
        roi.join(inc)
        .reset_index()
        .merge(spend, left_on="channel", right_index=True)
    )
    summary.insert(0, "model_version", model_version)
    summary = summary[[
        "model_version", "channel", "distribution", "spend",
        "roi_mean", "roi_median", "roi_ci_lo", "roi_ci_hi", "incremental_sales_mean",
    ]].sort_values(["distribution", "channel"])  # posterior rows first

    path.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(path, index=False)

    print("\n=== ROI (90% credible interval) ===")
    show = summary[summary["distribution"] == "posterior"]
    print(show[["channel", "roi_mean", "roi_ci_lo", "roi_ci_hi"]].round(2).to_string(index=False))
    print(f"Saved ROI summary to {path}")
    return summary


# ---------------------------------------------------------------------------
def main() -> None:
    start = time.time()

    df = load_mart(DATA_PATH)
    data = build_input_data(df)
    check_input_data(data, df)

    if REFIT:
        model_spec = build_model_spec()
        mmm = fit_model(data, model_spec)
        save_model(mmm, MODEL_PATH)
    else:
        mmm = load_model(MODEL_PATH)
    print(f"Model ready after {(time.time() - start) / 60:.1f} min")

    converged = report_convergence(mmm)
    report_chain_agreement(mmm)
    report_fit(mmm)
    save_roi_summary(mmm, ROI_PATH)
    if not converged:
        print("\nWARNING: chains did not fully converge. Before using the ROIs, check")
        print("'Posterior means by chain': if ROI agrees across chains, the problem is")
        print("confined to the parameters listed above, not the channel estimates.")


if __name__ == "__main__":
    main()