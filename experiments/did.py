"""
Step 2 of the geo experiment analysis: difference-in-differences (DiD).

Run from the project root with .venv-meridian active:
    python experiments/did.py

Reads:  data/mart_mmm__state_week.csv
Writes: outputs/experiment_estimates.csv  (rows with estimator = "did"; other
        estimators' rows are kept, so synthetic control can add its own)

The idea in one line: the change in treated states' sales from before to during
the pause, minus the change in control states' sales over the same weeks. The
control states' change stands in for "what would have happened anyway".

Model, on state-week rows (pre-period + test window only):

    log(sales_per_capita[g,t]) = state_effect[g] + week_effect[t]
                                 + delta * paused[g,t] + error

    paused = 1 for treated states during the test window, else 0.
    state_effect absorbs each state's own level (so no separate "treated" term),
    week_effect absorbs everything national that week (so no separate "test
    period" term). delta is what is left: the change specific to treated states
    during the pause. exp(delta) - 1 is the % change in their sales.

Weighting: each state-week is weighted by population, so the estimate is the %
change in TOTAL treated-state sales (big states count more), which is the
quantity that turns into lost dollars. It is also the same target synthetic
control estimates, since SC fits the treated states' combined sales. Logs
rather than dollar levels because growth is multiplicative: a state growing 4%
a year adds more dollars the bigger it is.

Key assumption: parallel trends. pre_trend_check.py shows it fails here, so
expect DiD to understate the loss, and more so with the longer pre-period.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

DATA_PATH = Path("data/mart_mmm__state_week.csv")
OUT_PATH = Path("outputs/experiment_estimates.csv")
PRE_WINDOWS = [26, 52]  # weeks of pre-period
Z_90 = 1.645            # 90% interval, matching the Meridian credible intervals


def load_panel(path: Path) -> tuple[pd.DataFrame, list[pd.Timestamp]]:
    df = pd.read_csv(path, parse_dates=["week_start"])
    df["log_sales_pc"] = np.log(df["net_sales"] / df["population"])
    df["paused"] = df["is_search_paused"].astype(int)
    test_weeks = sorted(df.loc[df["is_test_period"], "week_start"].unique())
    return df, test_weeks


def did_sample(df: pd.DataFrame, test_weeks: list, pre_weeks: int) -> pd.DataFrame:
    """Keep the pre_weeks before the test plus the test window. Weeks after the
    test are dropped: they hold carryover from the pause and later growth."""
    all_weeks = sorted(df["week_start"].unique())
    first_test = all_weeks.index(test_weeks[0])
    keep = all_weeks[first_test - pre_weeks: first_test] + list(test_weeks)
    return df[df["week_start"].isin(keep)].copy()


def fit_did(sample: pd.DataFrame):
    """Two-way fixed effects regression, population-weighted (WLS).

    Standard errors are clustered by state: a state's weeks are not independent
    (persistent demand shocks), and the treatment was assigned to states, so
    states are the unit of independence."""
    groups = pd.factorize(sample["state_code"])[0]
    return smf.wls(
        "log_sales_pc ~ paused + C(state_code) + C(week_start)",
        data=sample,
        weights=sample["population"],
    ).fit(cov_type="cluster", cov_kwds={"groups": groups})


def two_by_two(sample: pd.DataFrame) -> float:
    """The textbook four-number DiD on (population-weighted) group averages, as a
    check on the regression:
    (treated during - treated before) - (control during - control before)."""
    m = (
        sample.assign(wy=sample["log_sales_pc"] * sample["population"])
        .groupby(["is_treated_geo", "is_test_period"])[["wy", "population"]].sum()
        .pipe(lambda x: x["wy"] / x["population"])
    )
    return (m[(True, True)] - m[(True, False)]) - (m[(False, True)] - m[(False, False)])


def lost_revenue(sample: pd.DataFrame, delta: float) -> float:
    """Turn delta into dollars. If actual = counterfactual * exp(delta), then
    counterfactual - actual = actual * (exp(-delta) - 1), summed over the
    treated state-weeks in the test window."""
    actual = sample.loc[sample["paused"] == 1, "net_sales"].sum()
    return actual * (np.exp(-delta) - 1)


def save_rows(rows: pd.DataFrame, path: Path, estimator: str) -> None:
    """Replace this estimator's rows in the shared CSV; keep everyone else's."""
    if path.exists():
        existing = pd.read_csv(path)
        rows = pd.concat([existing[existing["estimator"] != estimator], rows], ignore_index=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows.to_csv(path, index=False)


def main() -> None:
    df, test_weeks = load_panel(DATA_PATH)
    print(f"Test window: {len(test_weeks)} weeks starting {pd.Timestamp(test_weeks[0]).date()}\n")

    rows = []
    for pre_weeks in PRE_WINDOWS:
        sample = did_sample(df, test_weeks, pre_weeks)
        fit = fit_did(sample)
        delta, se = fit.params["paused"], fit.bse["paused"]

        lo, hi = delta - Z_90 * se, delta + Z_90 * se
        lost = lost_revenue(sample, delta)
        # A more negative delta means a bigger loss, so the interval bounds swap.
        lost_lo, lost_hi = lost_revenue(sample, hi), lost_revenue(sample, lo)

        rows.append({
            "estimator": "did",
            "pre_weeks": pre_weeks,
            "delta_log": delta,
            "delta_se": se,
            "lift_pct": np.exp(delta) - 1,
            "lost_revenue": lost,
            "lost_revenue_lo": lost_lo,
            "lost_revenue_hi": lost_hi,
        })

        print(f"=== DiD, {pre_weeks}-week pre-period "
              f"({sample['week_start'].nunique()} weeks x {sample['state_code'].nunique()} states) ===")
        print(f"  delta = {delta:.4f} (clustered SE {se:.4f}); 2x2 check: {two_by_two(sample):.4f}")
        print(f"  pause changed treated sales by {np.exp(delta) - 1:+.2%} "
              f"(90% CI {np.exp(lo) - 1:+.2%} to {np.exp(hi) - 1:+.2%})")
        print(f"  lost revenue: ${lost / 1e6:.2f}M (90% CI ${lost_lo / 1e6:.2f}M to ${lost_hi / 1e6:.2f}M)\n")

    save_rows(pd.DataFrame(rows), OUT_PATH, estimator="did")
    print(f"Saved to {OUT_PATH}")


if __name__ == "__main__":
    main()
