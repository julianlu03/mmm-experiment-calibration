"""
Step 3 of the geo experiment analysis: synthetic control (SC).

Run from the project root:
    python experiments/synthetic_control.py

Reads:  data/mart_mmm__state_week.csv
Writes: outputs/experiment_estimates.csv   (rows with estimator = "synthetic_control")
        outputs/sc_weights.csv             (donor weights, 52-week fit)
        outputs/figures/synthetic_control.png

The idea: build a "synthetic treated group" as a weighted average of the 40
control states, with weights chosen so it tracks the treated states' sales
week by week BEFORE the test. During the test, that blend is the estimate of
what the treated states would have sold without the pause.

The optimization, over the pre-period weeks t:

    minimize    sum_t ( Y1[t] - sum_j w_j * Y0[t, j] )^2
    subject to  w_j >= 0  and  sum_j w_j = 1

    Y1 = treated group's sales per capita (population-weighted, as in DiD)
    Y0 = each control state's sales per capita
The constraints make the synthetic group a true average of real states: it can
interpolate between them but not extrapolate beyond them (the convex-hull
limit), and 40 free weights cannot overfit noise the way OLS would.

Unlike DiD there is no "difference" step: the synthetic group must match the
treated group's level AND its trend, so the gap during the test is the effect.

Uncertainty (placebo in space): SC has no standard error, so we run it where
nothing happened. 200 times, pick 10 random CONTROL states as a fake treated
group, build their synthetic control from the other 30, and measure the fake
"lift" during the test window. The spread (SD) of those fake lifts is how much
SC moves from noise alone; the 90% interval is the estimate +/- 1.645 SD.
Caveat: random fake groups are not fast-growers, so this measures noise, not
the risk of trend bias.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import minimize

from did import save_rows  # same folder; replaces only this estimator's rows

DATA_PATH = Path("data/mart_mmm__state_week.csv")
OUT_PATH = Path("outputs/experiment_estimates.csv")
WEIGHTS_PATH = Path("outputs/sc_weights.csv")
FIG_PATH = Path("outputs/figures/synthetic_control.png")
PRE_WINDOWS = [26, 52]  # same pre-periods as DiD, for a like-for-like comparison
MAIN_WINDOW = 52        # the one whose weights and figure we save
POST_WEEKS_SHOWN = 20   # weeks after the test to show in the figure
N_PLACEBO = 200
PLACEBO_SEED = 42
Z_90 = 1.645


def load_series(path: Path):
    """Weekly sales per capita: the treated group (one series) and each control state."""
    df = pd.read_csv(path, parse_dates=["week_start"])
    weeks = sorted(df["week_start"].unique())
    test_weeks = sorted(df.loc[df["is_test_period"], "week_start"].unique())

    # Treated group: sum sales and population across the 10 states, then divide
    # (population-weighted, the same target as the weighted DiD).
    treated = df[df["is_treated_geo"]].groupby("week_start")[["net_sales", "population"]].sum()
    y1 = treated["net_sales"] / treated["population"]
    treated_pop = treated["population"]

    controls = df[~df["is_treated_geo"]]
    y0 = (
        controls.assign(sales_pc=controls["net_sales"] / controls["population"])
        .pivot(index="week_start", columns="state_code", values="sales_pc")
    )
    control_pop = controls.groupby("state_code")["population"].first()
    n_treated = df.loc[df["is_treated_geo"], "state_code"].nunique()
    return y1, y0, treated_pop, weeks, test_weeks, control_pop, n_treated


def fit_weights(y1_pre: np.ndarray, y0_pre: np.ndarray):
    """Solve for donor weights with SLSQP.

    Both series are divided by the treated pre-period mean first, so values sit
    near 1.0. Raw per-capita sales (~$0.03) give squared errors near 1e-7,
    below SLSQP's default tolerance: it stops after a few iterations, reports
    success, and returns near-equal weights (which is just DiD again).
    """
    scale = y1_pre.mean()
    Y1, Y0 = y1_pre / scale, y0_pre / scale
    n_donors = Y0.shape[1]

    result = minimize(
        lambda w: np.sum((Y1 - Y0 @ w) ** 2),
        x0=np.full(n_donors, 1 / n_donors),                              # start from equal weights
        method="SLSQP",
        bounds=[(0, 1)] * n_donors,                                      # w_j >= 0
        constraints={"type": "eq", "fun": lambda w: w.sum() - 1},        # sum w_j = 1
        options={"maxiter": 1000},
    )
    if not result.success:
        raise RuntimeError(f"SLSQP did not converge: {result.message}")
    return result.x, result.nit


def placebo_lifts(y0, control_pop, pre, test_weeks, n_treated) -> np.ndarray:
    """Fake lifts from SC runs on random groups of control states (no real effect)."""
    rng = np.random.default_rng(PLACEBO_SEED)
    states = list(y0.columns)
    lifts = []
    for _ in range(N_PLACEBO):
        fake = list(rng.choice(states, n_treated, replace=False))
        donors = [s for s in states if s not in fake]
        pop = control_pop[fake]
        y_fake = (y0[fake] * pop).sum(axis=1) / pop.sum()  # population-weighted, like the real group
        w, _ = fit_weights(y_fake.loc[pre].to_numpy(), y0.loc[pre, donors].to_numpy())
        synth = y0.loc[test_weeks, donors].to_numpy() @ w
        lifts.append(y_fake.loc[test_weeks].sum() / synth.sum() - 1)
    return np.array(lifts)


def main() -> None:
    y1, y0, treated_pop, weeks, test_weeks, control_pop, n_treated = load_series(DATA_PATH)
    first_test = weeks.index(test_weeks[0])
    donors = list(y0.columns)
    print(f"Treated group vs {len(donors)} donor states; test window: "
          f"{len(test_weeks)} weeks starting {pd.Timestamp(test_weeks[0]).date()}\n")

    rows, saved = [], None
    for pre_weeks in PRE_WINDOWS:
        pre = weeks[first_test - pre_weeks: first_test]
        w, n_iter = fit_weights(y1.loc[pre].to_numpy(), y0.loc[pre].to_numpy())

        # Pre-period fit quality: root mean squared percentage error.
        synth_pre = y0.loc[pre].to_numpy() @ w
        rmspe = np.sqrt(np.mean(((y1.loc[pre] - synth_pre) / y1.loc[pre]) ** 2))

        # Effect during the test: synthetic (what would have happened) minus actual.
        synth_test = y0.loc[test_weeks].to_numpy() @ w
        actual_test = y1.loc[test_weeks].to_numpy()
        pop_test = treated_pop.loc[test_weeks].to_numpy()
        lost = np.sum((synth_test - actual_test) * pop_test)
        lift = np.sum(actual_test * pop_test) / np.sum(synth_test * pop_test) - 1

        # Placebo SD of the lift (share of synthetic sales), converted to dollars.
        fake = placebo_lifts(y0, control_pop, pre, test_weeks, n_treated)
        sd_dollars = fake.std(ddof=1) * np.sum(synth_test * pop_test)

        rows.append({
            "estimator": "synthetic_control",
            "pre_weeks": pre_weeks,
            "delta_log": np.log1p(lift),
            "lift_pct": lift,
            "lost_revenue": lost,
            "lost_revenue_lo": lost - Z_90 * sd_dollars,
            "lost_revenue_hi": lost + Z_90 * sd_dollars,
            "pre_rmspe": rmspe,
            "placebo_sd": fake.std(ddof=1),
        })

        top = pd.Series(w, index=donors).sort_values(ascending=False)
        print(f"=== Synthetic control, {pre_weeks}-week fit ({n_iter} SLSQP iterations) ===")
        print(f"  pre-period fit error (RMSPE): {rmspe:.2%}")
        print(f"  donors with weight > 1%: {(top > 0.01).sum()} of {len(donors)}; top 5: "
              + ", ".join(f"{s} {v:.2f}" for s, v in top.head(5).items()))
        print(f"  pause changed treated sales by {lift:+.2%} "
              f"(placebo SD {fake.std(ddof=1):.2%}, from {N_PLACEBO} fake groups; "
              f"mean fake lift {fake.mean():+.2%})")
        print(f"  lost revenue: ${lost / 1e6:.2f}M "
              f"(90% CI ${(lost - Z_90 * sd_dollars) / 1e6:.2f}M to ${(lost + Z_90 * sd_dollars) / 1e6:.2f}M)\n")

        if pre_weeks == MAIN_WINDOW:
            saved = (w, pre)

    save_rows(pd.DataFrame(rows), OUT_PATH, estimator="synthetic_control")
    w, pre = saved
    (pd.DataFrame({"state_code": donors, "weight": w})
     .sort_values("weight", ascending=False)
     .to_csv(WEIGHTS_PATH, index=False))
    plot(y1, y0, w, weeks, pre, test_weeks)
    print(f"Saved to {OUT_PATH}, {WEIGHTS_PATH}, {FIG_PATH}")


def plot(y1, y0, w, weeks, pre, test_weeks) -> None:
    """Actual vs synthetic per-capita sales: they should overlap before the test,
    separate during it (the lost sales), and come back together after."""
    last = weeks.index(test_weeks[-1]) + POST_WEEKS_SHOWN
    shown = weeks[weeks.index(pre[0]): last + 1]
    synth = pd.Series(y0.loc[shown].to_numpy() @ w, index=shown)

    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(shown, y1.loc[shown] * 100, color="#c0392b", lw=1.6, label="Treated states (actual)")
    ax.plot(shown, synth * 100, color="#2c3e50", lw=1.6, ls="--", label="Synthetic control")
    ax.axvspan(test_weeks[0], test_weeks[-1], color="#f1c40f", alpha=0.25, lw=0, label="Search paused")
    ax.set_ylabel("Net sales per 100 residents ($)")
    ax.set_title(f"Synthetic control: fit on {len(pre)} pre-test weeks, then projected through the pause")
    ax.legend(loc="upper left")

    FIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(FIG_PATH, dpi=150)


if __name__ == "__main__":
    main()
