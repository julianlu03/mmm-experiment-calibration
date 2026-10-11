"""
Step 4 of the geo experiment analysis: turn lost revenue into iROAS.

Run from the project root, AFTER did.py and synthetic_control.py:
    python experiments/iroas.py

Reads:  data/mart_mmm__state_week.csv
        outputs/experiment_estimates.csv
Writes: outputs/experiment_estimates.csv  (adds withheld_spend, iroas, iroas_lo, iroas_hi)

    iROAS = lost revenue / withheld spend

Withheld spend is a counterfactual: what paid search WOULD have spent in the
treated states during the test. Their actual spend was $0, so it has to be
estimated, just like the lost sales.

Method (control-scaled): the treated states' share of total search spend over
the 52 pre-test weeks, times the control states' ACTUAL search spend during the
test. The controls show what spend did nationally in those weeks (summer
seasonality, the July 4 promo), which a simple pre-test run rate would miss.

One withheld-spend figure is used for every estimator: it is a fact about the
campaign, not about how the sales loss was estimated, so differences in iROAS
come only from the lost-revenue estimates.

In practice, the planned media budget for the paused campaigns is the better
denominator; the simulated exports do not include one.
"""

from pathlib import Path

import pandas as pd

DATA_PATH = Path("data/mart_mmm__state_week.csv")
EST_PATH = Path("outputs/experiment_estimates.csv")
SPEND_PRE_WEEKS = 52


def estimate_withheld_spend(path: Path, pre_weeks: int) -> tuple[float, float]:
    df = pd.read_csv(path, parse_dates=["week_start"])
    weeks = sorted(df["week_start"].unique())
    test_weeks = sorted(df.loc[df["is_test_period"], "week_start"].unique())
    pre = weeks[weeks.index(test_weeks[0]) - pre_weeks: weeks.index(test_weeks[0])]

    spend = df.groupby(["week_start", "is_treated_geo"])["spend_paid_search"].sum().unstack()
    treated, control = spend[True], spend[False]
    assert treated.loc[test_weeks].sum() == 0, "Treated states should have $0 search during the test"

    treated_share = treated.loc[pre].sum() / control.loc[pre].sum()  # treated $ per control $
    withheld = treated_share * control.loc[test_weeks].sum()
    return withheld, treated_share


def main() -> None:
    withheld, ratio = estimate_withheld_spend(DATA_PATH, SPEND_PRE_WEEKS)
    print(f"Withheld search spend: ${withheld:,.0f} "
          f"(treated spent ${ratio:.3f} per $1 of control spend over {SPEND_PRE_WEEKS} pre-test weeks)\n")

    est = pd.read_csv(EST_PATH)
    est["withheld_spend"] = withheld
    est["iroas"] = est["lost_revenue"] / withheld
    # Interval bounds where the estimator has one (DiD now; SC after step 5).
    est["iroas_lo"] = est["lost_revenue_lo"] / withheld
    est["iroas_hi"] = est["lost_revenue_hi"] / withheld
    est.to_csv(EST_PATH, index=False)

    show = est[["estimator", "pre_weeks", "lost_revenue", "iroas", "iroas_lo", "iroas_hi"]].copy()
    show["lost_revenue"] = (show["lost_revenue"] / 1e6).map("${:.2f}M".format)
    print("=== iROAS by estimator (90% interval where available) ===")
    print(show.round(2).to_string(index=False))
    print(f"\nSaved to {EST_PATH}")


if __name__ == "__main__":
    main()
