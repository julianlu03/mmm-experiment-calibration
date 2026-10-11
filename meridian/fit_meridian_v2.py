"""
Meridian v2: v1 calibrated with the geo experiment.

Run from the project root with .venv-meridian active, AFTER the experiment
scripts (did.py, synthetic_control.py, iroas.py):
    python meridian/fit_meridian_v2.py

Same data, same model, same sampler settings as v1. One change: the paid search
ROI prior. v1 gave every channel Meridian's default LogNormal(0.2, 0.9)
(median 1.22, 90% range 0.28 to 5.37). v2 replaces it, for paid search only,
with the synthetic control result:

    iROAS 1.74, 90% interval 1.36 to 2.12  ->  mean 1.74, SD 0.23
    -> LogNormal(mu, sigma) with that mean and SD

Calibration window: the prior describes paid search ROI for media run DURING
the experiment (2024-06-03 to 2024-07-28), because that is what the experiment
measured, via Meridian's roi_calibration. Social and video keep the default
prior over the full period: the experiment says nothing about them.

The prior is read from outputs/experiment_estimates.csv (synthetic control,
52-week fit). Like v1, this script never reads the truth.

Reuses v1's loading, checks, diagnostics, and ROI summary, so the two models
differ only in the prior.
"""

import time
from pathlib import Path

import numpy as np
import pandas as pd

import fit_meridian_v1 as v1  # same folder; also sets the TF log level and warning filters
from meridian import backend
from meridian import constants
from meridian.model import model, prior_distribution, spec

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
ESTIMATES_PATH = Path("outputs/experiment_estimates.csv")
MODEL_PATH = Path("outputs/meridian_v2_model.binpb")
ROI_PATH = Path("outputs/meridian_v2_roi.csv")

CALIBRATED_CHANNEL = "paid_search"
EXPERIMENT_ESTIMATOR = "synthetic_control"
EXPERIMENT_PRE_WEEKS = 52
Z_90 = 1.645
DEFAULT_ROI_MU, DEFAULT_ROI_SIGMA = 0.2, 0.9  # Meridian's default roi_m prior

REFIT = True
# Run SMOKE first (a couple of minutes) to confirm the calibration is accepted,
# then FULL, which matches v1 exactly so the comparison is fair.
MCMC_SMOKE = {"n_chains": 2, "n_adapt": 50, "n_burnin": 50, "n_keep": 50}
MCMC_FULL = v1.MCMC_FULL
MCMC = MCMC_FULL


# ---------------------------------------------------------------------------
# 1. Turn the experiment result into a LogNormal prior
# ---------------------------------------------------------------------------
def experiment_prior(path: Path) -> tuple[float, float, float, float]:
    """Read the experiment iROAS and its 90% interval; return (mean, sd, mu, sigma).

    LogNormal(mu, sigma) has mean exp(mu + sigma^2 / 2) and
    variance (exp(sigma^2) - 1) * mean^2, so for a target mean m and SD s:
        sigma^2 = ln(1 + (s / m)^2)
        mu      = ln(m) - sigma^2 / 2
    """
    est = pd.read_csv(path)
    row = est[(est["estimator"] == EXPERIMENT_ESTIMATOR) & (est["pre_weeks"] == EXPERIMENT_PRE_WEEKS)]
    if len(row) != 1 or row["iroas_lo"].isna().any():
        raise ValueError(f"Run did.py, synthetic_control.py, and iroas.py first ({path})")
    row = row.iloc[0]

    mean = row["iroas"]
    sd = (row["iroas_hi"] - row["iroas_lo"]) / (2 * Z_90)
    sigma = np.sqrt(np.log(1 + (sd / mean) ** 2))
    mu = np.log(mean) - sigma**2 / 2
    return mean, sd, mu, sigma


def test_window(df: pd.DataFrame) -> tuple[str, str]:
    """First and last week_start of the experiment, as Meridian time coordinates."""
    weeks = pd.to_datetime(df.loc[df["is_test_period"], "week_start"])
    return weeks.min().strftime("%Y-%m-%d"), weeks.max().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# 2. ModelSpec: v1's spec plus the calibrated prior
# ---------------------------------------------------------------------------
def build_model_spec(channels: list[str], mu: float, sigma: float, start: str, end: str):
    """roi_m gets one (mu, sigma) per channel, in the InputData's channel order."""
    mus = [mu if ch == CALIBRATED_CHANNEL else DEFAULT_ROI_MU for ch in channels]
    sigmas = [sigma if ch == CALIBRATED_CHANNEL else DEFAULT_ROI_SIGMA for ch in channels]
    prior = prior_distribution.PriorDistribution(
        roi_m=backend.tfd.LogNormal(
            np.asarray(mus, dtype=backend.np_float_dtype),
            np.asarray(sigmas, dtype=backend.np_float_dtype),
            name=constants.ROI_M,
        )
    )
    # The paid search prior applies to ROI of media run inside the test window.
    calibration = spec.CalibrationSpec(spec=[
        spec.ChannelCalibrationSpec(
            channels=[CALIBRATED_CHANNEL],
            date_ranges=[spec.DateRange(start_date=start, end_date=end)],  # both ends inclusive
        )
    ])
    return spec.ModelSpec(
        prior=prior,
        knots=None,
        max_lag=8,
        media_prior_type="roi",
        roi_calibration=calibration,
    )


def fit_model(data, model_spec):
    mmm = model.Meridian(input_data=data, model_spec=model_spec)
    mmm.sample_prior(n_draws=v1.N_PRIOR_DRAWS, seed=v1.SEED)
    mmm.sample_posterior(**MCMC, seed=v1.SEED)
    return mmm


# ---------------------------------------------------------------------------
def main() -> None:
    start_time = time.time()

    df = v1.load_mart(v1.DATA_PATH)
    data = v1.build_input_data(df)
    v1.check_input_data(data, df)
    channels = list(data.media.coords["media_channel"].values)

    mean, sd, mu, sigma = experiment_prior(ESTIMATES_PATH)
    start, end = test_window(df)
    print(f"\nCalibrated prior for {CALIBRATED_CHANNEL}: LogNormal(mu={mu:.3f}, sigma={sigma:.3f}) "
          f"-> mean {mean:.2f}, SD {sd:.2f}, applied to media run {start} to {end} (week starts)")

    if REFIT:
        mmm = fit_model(data, build_model_spec(channels, mu, sigma, start, end))
        v1.save_model(mmm, MODEL_PATH)
    else:
        mmm = v1.load_model(MODEL_PATH)
    print(f"Model ready after {(time.time() - start_time) / 60:.1f} min")

    converged = v1.report_convergence(mmm)
    v1.report_chain_agreement(mmm)
    v1.report_fit(mmm)
    v1.save_roi_summary(mmm, ROI_PATH, model_version="v2")
    print("\nNote: summary ROI is over the full 156 weeks for every channel; the paid")
    print("search prior was set on the test window only.")
    if not converged:
        print("\nWARNING: chains did not fully converge. Check 'Posterior means by chain'.")


if __name__ == "__main__":
    main()
