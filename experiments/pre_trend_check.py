"""
Step 1 of the geo experiment analysis: do treated and control states trend in
parallel BEFORE the test? Difference-in-differences assumes they would have
kept moving together without the pause; this script checks that assumption.

Run from the project root with .venv-meridian active (it already has
statsmodels and matplotlib):
    python experiments/pre_trend_check.py

Reads:  data/mart_mmm__state_week.csv
Writes: outputs/figures/pre_trend.png

Method: compare per-capita sales of the treated group vs the control group.
Using the LOG RATIO log(treated / control) cancels everything the two groups
share (national seasonality, promos, the December peak), so what is left is
the gap between them. If trends are parallel, the gap is flat before the test.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm

DATA_PATH = Path("data/mart_mmm__state_week.csv")
FIG_PATH = Path("outputs/figures/pre_trend.png")
PRE_WINDOWS = [26, 52]  # weeks before the test; the DiD pre-periods we will compare


def load_groups(path: Path) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    """Weekly per-capita net sales for the treated and control groups."""
    df = pd.read_csv(path, parse_dates=["week_start"])

    test_weeks = df.loc[df["is_test_period"], "week_start"]
    test_start, test_end = test_weeks.min(), test_weeks.max()

    # Sum sales and population within each group, then divide: a population-
    # weighted per-capita figure, so big states count in proportion to size.
    df["group"] = np.where(df["is_treated_geo"], "treated", "control")
    weekly = (
        df.groupby(["week_start", "group"])[["net_sales", "population"]]
        .sum()
        .assign(sales_pc=lambda x: x["net_sales"] / x["population"])
        ["sales_pc"]
        .unstack("group")
    )
    weekly["log_ratio"] = np.log(weekly["treated"] / weekly["control"])
    weekly["t"] = np.arange(len(weekly))  # week index 0..155
    return weekly, test_start, test_end


def fit_pre_trend(weekly: pd.DataFrame, test_start: pd.Timestamp, n_weeks: int):
    """Regress the log ratio on a time trend over the n_weeks before the test.

    slope = how much faster the treated group grows per week (in log points,
    about % / 100). HAC (Newey-West) standard errors because week-to-week
    errors are correlated (demand shocks persist), which would make plain OLS
    standard errors too small.
    """
    pre = weekly[weekly.index < test_start].tail(n_weeks)
    X = sm.add_constant(pre["t"])
    return sm.OLS(pre["log_ratio"], X).fit(cov_type="HAC", cov_kwds={"maxlags": 4}), pre


def main() -> None:
    weekly, test_start, test_end = load_groups(DATA_PATH)
    test = weekly[(weekly.index >= test_start) & (weekly.index <= test_end)]

    print(f"Test window: weeks starting {test_start.date()} to {test_end.date()} ({len(test)} weeks)\n")
    print("=== Pre-trend: growth of treated relative to control ===")

    fits = {}
    for n in PRE_WINDOWS:
        fit, pre = fit_pre_trend(weekly, test_start, n)
        fits[n] = (fit, pre)
        slope, se, p = fit.params["t"], fit.bse["t"], fit.pvalues["t"]

        # What DiD assumes vs what the pre-trend implies for the test window.
        # DiD's counterfactual gap = the AVERAGE pre-period gap (flat line).
        # If the pre-trend continued, the gap during the test would be larger.
        did_gap = pre["log_ratio"].mean()
        trend_gap = fit.predict(sm.add_constant(test["t"])).mean()
        bias_share = trend_gap - did_gap  # log points ~ share of treated sales

        print(f"\n{n}-week pre-period ({pre.index.min().date()} to {pre.index.max().date()}):")
        print(f"  treated grows {slope * 100:+.3f}% per week faster than control "
              f"(SE {se * 100:.3f}%, p = {p:.3f})")
        print(f"  over {n} weeks that is a {slope * n * 100:+.1f}% relative gain")
        print(f"  if the trend continued, DiD's flat-gap counterfactual is too low by "
              f"~{bias_share * 100:.1f}% of treated sales during the test")

    plot(weekly, fits, test_start, test_end)


def plot(weekly, fits, test_start, test_end) -> None:
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 8), sharex=True)

    # Top: both groups, each indexed to its own first-26-weeks average = 100.
    base = weekly.iloc[:26][["treated", "control"]].mean()
    for col, color in [("treated", "#c0392b"), ("control", "#2c3e50")]:
        ax1.plot(weekly.index, weekly[col] / base[col] * 100, label=col, color=color, lw=1.2)
    ax1.set_ylabel("Per-capita sales\n(index, early 2023 = 100)")
    ax1.set_title("Treated vs control states: weekly per-capita net sales")
    ax1.legend(loc="upper left")

    # Bottom: the gap, with the 52-week pre-trend extended into the test window.
    ax2.plot(weekly.index, weekly["log_ratio"] * 100, color="#7f8c8d", lw=1, label="gap (treated vs control)")
    fit, pre = fits[max(fits)]
    span = weekly[(weekly.index >= pre.index.min()) & (weekly.index <= test_end)]
    ax2.plot(span.index, fit.predict(sm.add_constant(span["t"])) * 100,
             color="#c0392b", lw=2, label=f"{max(fits)}-week pre-trend, extended")
    ax2.hlines(pre["log_ratio"].mean() * 100, pre.index.min(), test_end,
               colors="#2c3e50", linestyles="--", lw=2, label="DiD assumption: flat gap")
    ax2.set_ylabel("log(treated / control) x 100\n(~ % gap)")
    ax2.set_title("The gap between them: rising before the test means parallel trends fails")
    ax2.legend(loc="upper left")

    for ax in (ax1, ax2):
        ax.axvspan(test_start, test_end, color="#f1c40f", alpha=0.25, lw=0)
    ax1.text(test_start, ax1.get_ylim()[1], " search paused", va="top", fontsize=9)

    FIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(FIG_PATH, dpi=150)
    print(f"\nSaved figure to {FIG_PATH}")


if __name__ == "__main__":
    main()