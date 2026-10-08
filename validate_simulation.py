"""
Check that the simulator actually produces the two failure modes, before any
real modeling happens. Uses only the truth layer; nothing here is part of the
analysis pipeline.

  1. Raw exports reconcile to the truth table (simulator self-test)
  2. Endogeneity: a regression that KNOWS the true adstock/saturation shapes
     still overstates paid search ROI, and the bias disappears when search
     spend stops following hidden demand.
  3. Parallel trends: DiD misses the true experiment lift; synthetic control
     gets closer. Checked across many seeds so it isn't one lucky draw.
  4. Platform-reported ROAS is inflated versus true ROI.

Writes validation_report.md.
"""

import copy
import json

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from config import CHANNELS, TRUE_PARAMS
from simulate import experiment_truth, simulate_world

N_SEEDS = 20


# --------------------------------------------------------------------------
# 1. Raw exports reconcile to truth
# --------------------------------------------------------------------------

def reconcile(raw_dir="data/raw", truth_path="data/truth/truth_geo_week.csv"):
    truth = pd.read_csv(truth_path)
    census = pd.read_csv(f"{raw_dir}/census_state_population.csv")
    name_to_code = dict(zip(census.state_name, census.state_code))

    def monday(s):
        d = pd.to_datetime(s)
        return (d - pd.to_timedelta(d.dt.weekday, unit="D")).dt.strftime("%Y-%m-%d")

    g = pd.read_csv(f"{raw_dir}/google_ads_geo_daily.csv").drop_duplicates()
    g = g[g.geo_target_state != "Unknown"]
    g["state_code"] = g.geo_target_state.str.replace(", United States", "").map(name_to_code)
    g["channel"] = np.where(g.campaign_advertising_channel_type == "SEARCH", "paid_search", "online_video")
    g["week_start"], g["spend"] = monday(g.segments_date), g.metrics_cost_micros / 1e6

    m = pd.read_csv(f"{raw_dir}/meta_ads_region_daily.csv")
    m["state_code"], m["channel"] = m.Region.map(name_to_code), "paid_social"
    m["week_start"], m["spend"] = monday(m.Day), m["Amount spent (USD)"]

    spend = (pd.concat([g[["state_code", "week_start", "channel", "spend"]],
                        m[["state_code", "week_start", "channel", "spend"]]])
             .groupby(["state_code", "week_start", "channel"]).spend.sum().unstack(fill_value=0))

    s = pd.read_csv(f"{raw_dir}/shopify_sales_daily.csv", keep_default_na=False)
    s = s[s.billing_region_code != ""]
    s["week_start"] = monday(s.day)
    s["net"] = s.gross_sales + s.discounts + s.returns
    rev = s.groupby(["billing_region_code", "week_start"]).net.sum()
    rev.index.names = ["state_code", "week_start"]

    t = truth.set_index(["state_code", "week_start"])
    out = {"revenue": float((t.revenue - rev.reindex(t.index, fill_value=0)).abs().max())}
    for c in CHANNELS:
        out[c] = float((t[f"spend_{c}"] - spend[c].reindex(t.index, fill_value=0)).abs().max())
    return out


# --------------------------------------------------------------------------
# 2. Endogeneity check
# --------------------------------------------------------------------------

def _two_way_demean(a):
    return a - a.mean(axis=1, keepdims=True) - a.mean(axis=0, keepdims=True) + a.mean()


def oracle_regression_roi(w):
    """Per-capita revenue on geo FE + week FE + the TRUE media transforms.
    A best case for MMM: the functional forms are known, only the coefficients are estimated."""
    pop = w["pop"][:, None]
    y = _two_way_demean(w["revenue"] / pop).ravel()
    H = {c: w["contrib"][c] / w["beta"][c] for c in CHANNELS}
    X = np.column_stack([_two_way_demean(H[c] / pop).ravel() for c in CHANNELS])
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    return {c: float(b[i] * H[c].sum() / w["actual"][c].sum()) for i, c in enumerate(CHANNELS)}


# --------------------------------------------------------------------------
# 3. Experiment: DiD vs synthetic control
# --------------------------------------------------------------------------

def per_capita(w, mask):
    return w["revenue"][mask].sum(axis=0) / w["pop"][mask].sum()


def did_lost_pc(w, pre_weeks):
    tr, w0, w1 = w["is_treated"], w["w0"], w["w1"]
    T_, C_ = per_capita(w, tr), per_capita(w, ~tr)
    pre, post = slice(w0 - pre_weeks, w0), slice(w0, w1)
    return -((T_[post].mean() - T_[pre].mean()) - (C_[post].mean() - C_[pre].mean()))


def synthetic_control_lost_pc(w, pre_weeks=52):
    tr, w0, w1 = w["is_treated"], w["w0"], w["w1"]
    target = per_capita(w, tr)
    donors = (w["revenue"][~tr] / w["pop"][~tr][:, None])
    pre = slice(w0 - pre_weeks, w0)
    Y, D = target[pre], donors[:, pre]
    n = D.shape[0]
    res = minimize(lambda v: np.sum((Y - v @ D) ** 2), np.full(n, 1 / n), method="SLSQP",
                   bounds=[(0, 1)] * n, constraints={"type": "eq", "fun": lambda v: v.sum() - 1},
                   options={"maxiter": 500, "ftol": 1e-12})
    cf = res.x @ donors
    pre_rmspe = float(np.sqrt(np.mean((Y - cf[pre]) ** 2)) / Y.mean())
    return float((cf[w0:w1] - target[w0:w1]).mean()), pre_rmspe, res.x


def pre_trend_gap(w, pre_weeks=52):
    """Weekly slope difference (treated minus control) in per-capita revenue before the test, % of level."""
    tr, w0 = w["is_treated"], w["w0"]
    gap = per_capita(w, tr) / per_capita(w, tr)[w0 - pre_weeks:w0].mean() \
        - per_capita(w, ~tr) / per_capita(w, ~tr)[w0 - pre_weeks:w0].mean()
    x = np.arange(pre_weeks)
    return float(np.polyfit(x, gap[w0 - pre_weeks:w0], 1)[0])


def experiment_estimates(w):
    truth = experiment_truth(w)
    scale = truth["treated_pop"] * (w["w1"] - w["w0"]) / truth["withheld_spend"]  # per-capita-week -> iROAS
    sc, rmspe, _ = synthetic_control_lost_pc(w)
    return {
        "true": truth["true_iroas_in_window"],
        "did_26": did_lost_pc(w, 26) * scale,
        "did_52": did_lost_pc(w, 52) * scale,
        "sc_52": sc * scale,
        "sc_pre_rmspe": rmspe,
        "pre_trend_gap": pre_trend_gap(w),
    }


# --------------------------------------------------------------------------

def fmt(x, d=2):
    return f"{x:.{d}f}"


def main():
    lines = ["# Simulation validation report", "",
             f"Default seed {TRUE_PARAMS['seed']}, plus {N_SEEDS} seeds for robustness. "
             "Generated by `validate_simulation.py`.", ""]

    # 1. Reconciliation
    r = reconcile()
    lines += ["## 1. Raw exports reconcile to truth", "",
              "Max absolute geo-week difference after a reference cleaning (dedupe, drop unknown geos, "
              "Monday weeks, net = gross + discounts + returns):", "",
              "| Series | Max abs diff ($) |", "|---|---|"]
    lines += [f"| {k} | {v:.2f} |" for k, v in r.items()]
    lines += ["", "Any value above ~$0.01 means the exports and truth disagree.", ""]

    # 2. Endogeneity
    rows = {"endogenous": [], "exogenous": []}
    for i in range(N_SEEDS):
        seed = TRUE_PARAMS["seed"] + i
        for label, elast in [("endogenous", None), ("exogenous", 0.0)]:
            p = copy.deepcopy(TRUE_PARAMS)
            if elast is not None:
                p["channels"]["paid_search"]["demand_elasticity"] = elast
            rows[label].append(oracle_regression_roi(simulate_world(p, seed=seed)))
    lines += ["## 2. Endogeneity biases paid search upward", "",
              "Oracle regression: per-capita revenue on state FE + week FE + the true adstock/Hill "
              "transforms. This is a best case (known functional forms), not Meridian.", "",
              "| Channel | True ROI | Estimated, search follows hidden demand | Estimated, search exogenous |",
              "|---|---|---|---|"]
    for c in CHANNELS:
        en = np.array([r_[c] for r_ in rows["endogenous"]])
        ex = np.array([r_[c] for r_ in rows["exogenous"]])
        lines.append(f"| {c} | {TRUE_PARAMS['channels'][c]['roi']} | "
                     f"{fmt(en[0])} (mean {fmt(en.mean())}, sd {fmt(en.std())}) | "
                     f"{fmt(ex[0])} (mean {fmt(ex.mean())}, sd {fmt(ex.std())}) |")
    lines += ["", "First number is the default seed; mean/sd across seeds in parentheses. "
              "The only difference between the two columns is whether search spend responds to "
              "the hidden demand shock.", ""]

    # 3. Experiment
    exp = [experiment_estimates(simulate_world(TRUE_PARAMS, seed=TRUE_PARAMS["seed"] + i)) for i in range(N_SEEDS)]
    e = pd.DataFrame(exp)
    for k in ["did_26", "did_52", "sc_52"]:
        e[f"{k}_err"] = e[k] - e["true"]
    d0 = e.iloc[0]
    lines += ["## 3. Geo holdout: DiD vs synthetic control", "",
              "iROAS = lost revenue in treated states during the 8 weeks / withheld search spend.", "",
              "| Estimator | Default seed | Mean error across seeds | Mean abs error |", "|---|---|---|---|",
              f"| Truth | {fmt(d0.true)} | - | - |"]
    for k, label in [("did_26", "DiD, 26-week pre-period"), ("did_52", "DiD, 52-week pre-period"),
                     ("sc_52", "Synthetic control, 52-week fit")]:
        lines.append(f"| {label} | {fmt(d0[k])} | {fmt(e[k + '_err'].mean())} | {fmt(e[k + '_err'].abs().mean())} |")
    lines += ["",
              f"Pre-period trend gap (treated minus control, per week, share of level): default seed "
              f"{d0.pre_trend_gap * 100:.3f}%, mean {e.pre_trend_gap.mean() * 100:.3f}%. "
              f"Synthetic control pre-period fit error (RMSPE): {d0.sc_pre_rmspe:.2%}.", ""]

    # 4. Platform ROAS
    with open("data/truth/truth_summary.json") as f:
        s = json.load(f)
    g = pd.read_csv("data/raw/google_ads_geo_daily.csv").drop_duplicates()
    g = g[g.geo_target_state != "Unknown"]
    m = pd.read_csv("data/raw/meta_ads_region_daily.csv")
    plat = {
        "paid_search": g[g.campaign_advertising_channel_type == "SEARCH"],
        "online_video": g[g.campaign_advertising_channel_type == "VIDEO"],
    }
    roas = {c: d.metrics_conversions_value.sum() / (d.metrics_cost_micros.sum() / 1e6) for c, d in plat.items()}
    roas["paid_social"] = m["Purchases conversion value"].sum() / m["Amount spent (USD)"].sum()
    lines += ["## 4. Platform-reported ROAS vs truth", "", "| Channel | Platform ROAS (from exports) | True ROI |",
              "|---|---|---|"]
    lines += [f"| {c} | {fmt(roas[c])} | {fmt(s['channels'][c]['true_roi'])} |" for c in CHANNELS]
    lines.append("")

    report = "\n".join(lines)
    with open("validation_report.md", "w") as f:
        f.write(report)
    print(report)


if __name__ == "__main__":
    main()
