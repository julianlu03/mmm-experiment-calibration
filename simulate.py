"""
Simulate a DTC e-commerce brand with known ground truth, then write the
messy raw exports an analyst would actually receive.

Two layers:
  1. simulate_world()      -> the true geo x week world (baseline demand, spend,
                              media effects, the geo experiment). Pure numpy.
  2. build_raw_exports()   -> Google Ads, Meta Ads, Shopify, promo calendar,
                              experiment design and census population files,
                              in their own schemas, grains and quirks.

Outputs
  data/raw/    what the analyst gets (loaded to BigQuery, cleaned in dbt)
  data/truth/  what only the evaluator sees (never used for modeling)

Usage
  python simulate.py                 # default seed from config
  python simulate.py --seed 7        # a different world, same parameters
"""

import argparse
import copy
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from config import CHANNELS, STATES, TRUE_PARAMS


# --------------------------------------------------------------------------
# Calendar helpers
# --------------------------------------------------------------------------

def _nth_weekday(year, month, weekday, n):
    """n-th given weekday (Mon=0) of a month; n=-1 means the last one."""
    if n > 0:
        d = date(year, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def build_promo_calendar(years):
    """The marketing team's promo calendar: (name, start, end inclusive, discount %)."""
    rows = []
    for y in years:
        memorial = _nth_weekday(y, 5, 0, -1)
        labor = _nth_weekday(y, 9, 0, 1)
        thanksgiving = _nth_weekday(y, 11, 3, 4)
        rows += [
            ("New Year Sale", date(y, 1, 1), date(y, 1, 7), 15),
            ("Spring Sale", date(y, 3, 14), date(y, 3, 20), 20),
            ("Memorial Day Sale", memorial - timedelta(days=3), memorial, 15),
            ("Fourth of July Sale", date(y, 7, 2), date(y, 7, 5), 15),
            ("Labor Day Sale", labor - timedelta(days=3), labor, 15),
            ("Black Friday / Cyber Monday", thanksgiving, thanksgiving + timedelta(days=4), 30),
            ("Holiday Gift Push", date(y, 12, 8), date(y, 12, 14), 20),
        ]
    return pd.DataFrame(rows, columns=["promo_name", "start_date", "end_date", "discount_pct"])


def seasonality(week_starts):
    """Smooth annual demand curve: modest summer bump, big December peak, January dip."""
    mid = pd.to_datetime(week_starts) + pd.Timedelta(days=3)
    doy = mid.dayofyear.to_numpy()
    s = (1.0
         + 0.08 * np.cos(2 * np.pi * (doy - 200) / 365)
         + 0.30 * np.exp(-(((doy - 340) / 18.0) ** 2))
         - 0.10 * np.exp(-(((doy - 12) / 15.0) ** 2)))
    return s


# --------------------------------------------------------------------------
# Media transforms (same functional forms Meridian uses)
# --------------------------------------------------------------------------

def geometric_adstock(x, decay, max_lag):
    """Normalized geometric adstock along the time axis. x: (G, T)."""
    w = decay ** np.arange(max_lag)
    w = w / w.sum()
    out = np.zeros_like(x)
    for lag, wl in enumerate(w):
        out[:, lag:] += wl * x[:, : x.shape[1] - lag]
    return out


def hill(x, ec50, slope):
    xs = np.power(np.clip(x, 0, None), slope)
    return xs / (xs + ec50 ** slope)


def media_response(spend, scale, ch, max_lag):
    """Unscaled response H = S * hill(adstock(spend) / S). Contribution = beta * H."""
    a = geometric_adstock(spend, ch["adstock_decay"], max_lag)
    return scale[:, None] * hill(a / scale[:, None], ch["hill_ec50"], ch["hill_slope"])


# --------------------------------------------------------------------------
# Layer 1: the true world
# --------------------------------------------------------------------------

def _mean_one_lognormal(rng, sd, size):
    return np.exp(rng.normal(-0.5 * sd ** 2, sd, size))


def _flight_schedule(rng, n_weeks, on_rng, off_rng):
    on = np.zeros(n_weeks, dtype=bool)
    t = int(rng.integers(0, off_rng[1] + 1))  # random phase
    while t < n_weeks:
        length = int(rng.integers(on_rng[0], on_rng[1] + 1))
        on[t: t + length] = True
        t += length + int(rng.integers(off_rng[0], off_rng[1] + 1))
    return on


def select_treated_geos(rng, pop, trend, exp_cfg):
    """'Growth markets' selection: mid-sized states with the fastest trends."""
    lo, hi = exp_cfg["candidate_pop_range"]
    candidates = np.where((pop >= lo) & (pop <= hi))[0]
    pool = candidates[np.argsort(-trend[candidates])][: exp_cfg["candidate_pool_size"]]
    return np.sort(rng.choice(pool, exp_cfg["n_treated_geos"], replace=False))


def simulate_world(params, seed=None):
    p = params
    rng = np.random.default_rng(p["seed"] if seed is None else seed)

    codes = np.array([s[0] for s in STATES])
    regions = np.array([s[2] for s in STATES])
    pop = np.array([s[3] for s in STATES], dtype=float)
    G, T = len(STATES), p["n_weeks"]
    t = np.arange(T)

    week_starts = pd.date_range(p["start_date"], periods=T, freq="7D")
    days = pd.date_range(p["start_date"], periods=T * 7, freq="D")

    # Promo days -> share of each week that is on promo
    promos = build_promo_calendar(sorted(set(days.year)))
    promo_daily = np.zeros(len(days), dtype=bool)
    for _, r in promos.iterrows():
        promo_daily |= (days.date >= r.start_date) & (days.date <= r.end_date)
    promo_frac = promo_daily.reshape(T, 7).mean(axis=1)

    season = seasonality(week_starts)

    # ---- Baseline demand ----
    b = p["baseline"]
    level = _mean_one_lognormal(rng, b["geo_level_sd"], G)
    trend = rng.normal(b["trend_mean"], b["trend_sd"], G)

    ds = p["demand_shock"]
    innov_sd = ds["sd"] * np.sqrt(1 - ds["ar1"] ** 2)
    shock = np.zeros((G, T))
    shock[:, 0] = rng.normal(0, ds["sd"], G)
    for k in range(1, T):
        shock[:, k] = ds["ar1"] * shock[:, k - 1] + rng.normal(0, innov_sd, G)

    noise = _mean_one_lognormal(rng, b["noise_sd"], (G, T))
    baseline = (pop[:, None] * b["revenue_per_capita_weekly"] * level[:, None]
                * season[None, :] * (1 + b["promo_lift"] * promo_frac)[None, :]
                * np.exp(trend[:, None] * t[None, :]) * np.exp(shock) * noise)

    # ---- Business-as-usual spend ----
    region_names = sorted(set(regions))
    bau = {}
    for c in CHANNELS:
        ch = p["channels"][c]
        geo_mult = _mean_one_lognormal(rng, 0.15, G)
        base = ch["spend_share"] * b["revenue_per_capita_weekly"] * pop * geo_mult
        seasonal = season ** ch["season_exponent"] * (1 + ch["promo_boost"] * promo_frac)
        e = ch["demand_elasticity"]
        endog = np.exp(e * shock - 0.5 * (e * ds["sd"]) ** 2)  # mean ~1, follows hidden demand
        x = base[:, None] * seasonal[None, :] * endog * _mean_one_lognormal(rng, ch["weekly_noise_sd"], (G, T))
        if "flighting" in ch:
            f = ch["flighting"]
            sched = {r: _flight_schedule(rng, T, f["on_weeks"], f["off_weeks"]) for r in region_names}
            on = np.stack([sched[r] for r in regions])
            x = x * on / on.mean()  # keep average spend share while bursting
        bau[c] = np.round(x, 2)

    # ---- Geo experiment: pause search in treated geos ----
    ex = p["experiment"]
    treated = select_treated_geos(rng, pop, trend, ex)
    is_treated = np.zeros(G, dtype=bool)
    is_treated[treated] = True
    w0, w1 = ex["start_week"], ex["start_week"] + ex["duration_weeks"]
    is_test_week = (t >= w0) & (t < w1)

    actual = {c: bau[c].copy() for c in CHANNELS}
    actual[ex["channel"]][np.ix_(is_treated, is_test_week)] = 0.0

    # ---- Media effects ----
    L = p["adstock_max_lag"]
    contrib, contrib_bau, beta, scale = {}, {}, {}, {}
    for c in CHANNELS:
        ch = p["channels"][c]
        x = bau[c]
        scale[c] = np.array([x[g][x[g] > 0].mean() for g in range(G)])
        H = media_response(actual[c], scale[c], ch, L)
        beta[c] = ch["roi"] * actual[c].sum() / H.sum()  # pins realized ROI to the target exactly
        contrib[c] = beta[c] * H
        contrib_bau[c] = beta[c] * media_response(bau[c], scale[c], ch, L)

    revenue = np.round(baseline + sum(contrib.values()), 2)

    return dict(
        params=p, codes=codes, regions=regions, pop=pop, week_starts=week_starts, days=days,
        promos=promos, promo_daily=promo_daily, promo_frac=promo_frac, season=season,
        level=level, trend=trend, shock=shock, baseline=baseline,
        bau=bau, actual=actual, contrib=contrib, contrib_bau=contrib_bau, beta=beta, scale=scale,
        revenue=revenue, is_treated=is_treated, is_test_week=is_test_week, w0=w0, w1=w1,
    )


def experiment_truth(world, tail_weeks=4):
    """What the geo test would measure with perfect knowledge of the counterfactual."""
    c = world["params"]["experiment"]["channel"]
    tr, w0, w1 = world["is_treated"], world["w0"], world["w1"]
    lost = world["contrib_bau"][c] - world["contrib"][c]
    withheld = world["bau"][c][tr, w0:w1].sum()
    lost_window = lost[tr, w0:w1].sum()
    lost_tail = lost[tr, w0:w1 + tail_weeks].sum()
    pop_t = world["pop"][tr].sum()
    return {
        "channel": c,
        "treated_geos": world["codes"][tr].tolist(),
        "test_start": str(world["week_starts"][w0].date()),
        "test_end": str((world["week_starts"][w1 - 1] + pd.Timedelta(days=6)).date()),
        "withheld_spend": round(float(withheld), 2),
        "lost_revenue_in_window": round(float(lost_window), 2),
        "lost_revenue_incl_carryover": round(float(lost_tail), 2),
        "true_iroas_in_window": round(float(lost_window / withheld), 4),
        "true_iroas_incl_carryover": round(float(lost_tail / withheld), 4),
        "lost_revenue_per_capita_week": float(lost_window / pop_t / (w1 - w0)),
        "treated_pop": float(pop_t),
        "share_of_treated_revenue_lost": round(float(lost_window / (world["revenue"][tr, w0:w1].sum() + lost_window)), 4),
    }


def truth_tables(world):
    p = world["params"]
    G, T = world["baseline"].shape
    idx = pd.MultiIndex.from_product([world["codes"], world["week_starts"]], names=["state_code", "week_start"])
    df = pd.DataFrame(index=idx).reset_index()
    df["population"] = np.repeat(world["pop"], T).astype(int)
    df["is_treated_geo"] = np.repeat(world["is_treated"], T)
    df["is_test_week"] = np.tile(world["is_test_week"], G)
    df["promo_frac"] = np.tile(world["promo_frac"], G)
    df["hidden_demand_shock"] = world["shock"].ravel()
    df["baseline_revenue"] = world["baseline"].ravel()
    for c in CHANNELS:
        df[f"spend_{c}"] = world["actual"][c].ravel()
        df[f"bau_spend_{c}"] = world["bau"][c].ravel()
        df[f"contribution_{c}"] = world["contrib"][c].ravel()
    df["revenue"] = world["revenue"].ravel()
    df["week_start"] = df["week_start"].dt.date

    total_rev = world["revenue"].sum()
    channels = {}
    for c in CHANNELS:
        ch = p["channels"][c]
        spend = world["actual"][c].sum()
        inc = world["contrib"][c].sum()
        channels[c] = {
            "true_roi": round(float(inc / spend), 4),
            "total_spend": round(float(spend), 2),
            "incremental_revenue": round(float(inc), 2),
            "share_of_revenue": round(float(inc / total_rev), 4),
            "spend_share_of_revenue": round(float(spend / total_rev), 4),
            "platform_overreport_factor": p["platform_overreport"][c],
            "adstock_decay": ch["adstock_decay"],
            "hill_ec50_relative_to_avg_spend": ch["hill_ec50"],
            "hill_slope": ch["hill_slope"],
        }
    summary = {
        "seed": p["seed"],
        "total_revenue": round(float(total_rev), 2),
        "baseline_share_of_revenue": round(float(world["baseline"].sum() / total_rev), 4),
        "channels": channels,
        "experiment": experiment_truth(world),
        "notes": [
            "ROI = incremental net sales / spend over the full 156 weeks.",
            "true_iroas_in_window is the ROI a perfect experiment measures: pausing to zero spend "
            "in spring, so it reflects average (not marginal) ROI at spring spend levels.",
            "Platform-reported ROAS is not truth; compute it from the raw exports (observable data).",
            "Hill EC50 is relative to each geo-channel's average weekly spend; Meridian normalizes "
            "differently, so compare ROI and response curves, not raw EC50 values.",
        ],
    }
    return df, summary


# --------------------------------------------------------------------------
# Layer 2: messy raw exports
# --------------------------------------------------------------------------

DOW_SPEND = np.array([1.05, 1.05, 1.02, 1.0, 0.95, 0.93, 1.0])  # Mon..Sun
DOW_SALES = np.array([1.10, 1.02, 1.0, 0.98, 0.95, 0.95, 1.0])


def split_weekly_to_daily_cents(weekly, dow, rng, promo_daily=None, promo_mult=1.0, conc=300.0):
    """Split (G, T) weekly dollars into (G, T*7) daily integer cents that sum exactly to the week."""
    G, T = weekly.shape
    w = np.tile(dow, T)
    if promo_daily is not None:
        w = w * np.where(promo_daily, promo_mult, 1.0)
    w = (w.reshape(T, 7) / w.reshape(T, 7).sum(axis=1, keepdims=True))
    g = rng.gamma(np.broadcast_to(conc * w, (G, T, 7)))
    frac = g / g.sum(axis=2, keepdims=True)
    target = np.round(weekly * 100).astype(np.int64)
    cents = np.floor(target[:, :, None] * frac).astype(np.int64)
    resid = target - cents.sum(axis=2)
    cents[np.arange(G)[:, None], np.arange(T)[None, :], frac.argmax(axis=2)] += resid
    return cents.reshape(G, T * 7)


def _long(arr_gd, codes, days, value_name):
    G, D = arr_gd.shape
    return pd.DataFrame({
        "state_code": np.repeat(codes, D),
        "day": np.tile(days.strftime("%Y-%m-%d"), G),
        value_name: arr_gd.ravel(),
    })


def build_raw_exports(world, rng):
    p = world["params"]
    m = p["messiness"]
    codes, days = world["codes"], world["days"]
    names = dict((s[0], s[1]) for s in STATES)
    G, T = world["revenue"].shape
    D = T * 7
    aov = m["avg_order_value"]

    # Platform-reported value per geo-week (attribution, inflated, noisy)
    reported = {c: world["contrib"][c] * p["platform_overreport"][c] * _mean_one_lognormal(rng, 0.10, (G, T))
                for c in CHANNELS}

    spend_daily = {c: split_weekly_to_daily_cents(world["actual"][c], DOW_SPEND, rng) for c in CHANNELS}

    def reported_daily(c, spend_cents_part):
        """Allocate the weekly reported value to days/campaigns in proportion to spend."""
        wk_spend = spend_daily[c].reshape(G, T, 7).sum(axis=2)
        val_per_cent = np.divide(reported[c], wk_spend, out=np.zeros_like(reported[c]), where=wk_spend > 0)
        return spend_cents_part * np.repeat(val_per_cent, 7, axis=1)  # dollars

    # ---------------- Google Ads (search brand, search non-brand, YouTube) ----------------
    s = spend_daily["paid_search"]
    brand_share = np.clip(rng.normal(m["brand_search_share"], 0.03, s.shape), 0.15, 0.45)
    brand = np.round(s * brand_share).astype(np.int64)
    campaigns = [
        (110234567, "US | Search | Brand | Exact", "SEARCH", brand, "paid_search", 1.10),
        (110234588, "US | Search | NonBrand | Category", "SEARCH", s - brand, "paid_search", 1.60),
        (110235120, "US | YouTube | Prospecting | In-Stream", "VIDEO", spend_daily["online_video"], "online_video", None),
    ]
    frames = []
    for cid, cname, ctype, cents, c, cpc in campaigns:
        df = _long(cents, codes, days, "cost_cents")
        df["geo_target_state"] = df["state_code"].map(names) + ", United States"
        df["conv_value"] = reported_daily(c, cents).ravel()
        df["campaign_id"], df["campaign_name"], df["campaign_type"], df["cpc"] = cid, cname, ctype, cpc
        frames.append(df)
    gads = pd.concat(frames, ignore_index=True)

    # Spend Google could not geo-locate (real money, but not attributable to a state)
    nat = pd.DataFrame({"day": days.strftime("%Y-%m-%d")})
    unk = []
    for cid, cname, ctype, cents, c, cpc in campaigns:
        u = nat.copy()
        u["cost_cents"] = np.round(cents.sum(axis=0) * m["google_ads_unknown_geo_share"]
                                   * _mean_one_lognormal(rng, 0.2, D)).astype(np.int64)
        u["conv_value"] = u["cost_cents"] / 100 * 0.5
        u["geo_target_state"], u["campaign_id"], u["campaign_name"], u["campaign_type"], u["cpc"] = \
            "Unknown", cid, cname, ctype, cpc
        unk.append(u)
    gads = pd.concat([gads] + unk, ignore_index=True)
    gads = gads[gads["cost_cents"] > 0].copy()  # platforms don't export zero-spend rows

    cost = gads["cost_cents"] / 100
    is_search = gads["campaign_type"] == "SEARCH"
    base_cpc = gads["cpc"].astype(float).fillna(1.0).to_numpy()
    cpc = np.where(is_search, base_cpc, 0.08) * _mean_one_lognormal(rng, 0.15, len(gads))
    clicks = np.where(is_search, cost / cpc, 0)
    impressions = np.where(is_search, clicks / 0.05, cost / 12.0 * 1000) * _mean_one_lognormal(rng, 0.1, len(gads))
    clicks = np.where(is_search, clicks, impressions * 0.003)
    google_ads = pd.DataFrame({
        "segments_date": gads["day"],
        "campaign_id": gads["campaign_id"],
        "campaign_name": gads["campaign_name"],
        "campaign_advertising_channel_type": gads["campaign_type"],
        "geo_target_state": gads["geo_target_state"],
        "metrics_cost_micros": gads["cost_cents"].astype(np.int64) * 10_000,
        "metrics_impressions": np.round(impressions).astype(np.int64),
        "metrics_clicks": np.round(clicks).astype(np.int64),
        "metrics_conversions": np.round(gads["conv_value"] / aov, 2),
        "metrics_conversions_value": np.round(gads["conv_value"], 2),
    })
    d0, d1 = m["google_ads_duplicate_dates"]
    dup = google_ads[(google_ads["segments_date"] >= d0) & (google_ads["segments_date"] <= d1)]
    google_ads = (pd.concat([google_ads, dup], ignore_index=True)
                  .sort_values(["segments_date", "campaign_id", "geo_target_state"], kind="stable")
                  .reset_index(drop=True))

    # ---------------- Meta Ads (prospecting, retargeting) ----------------
    s = spend_daily["paid_social"]
    rt_share = np.clip(rng.normal(m["meta_retargeting_share"], 0.04, s.shape), 0.1, 0.5)
    rt = np.round(s * rt_share).astype(np.int64)
    frames = []
    for cid, cname, cents in [("120208877001230045", "Prospecting - Broad - Purchase", s - rt),
                              ("120208877001230078", "Retargeting - Site Visitors 30D", rt)]:
        df = _long(cents, codes, days, "cost_cents")
        df["value"] = reported_daily("paid_social", cents).ravel()
        df["cid"], df["cname"] = cid, cname
        frames.append(df)
    meta = pd.concat(frames, ignore_index=True)
    meta = meta[meta["cost_cents"] > 0]
    spend = meta["cost_cents"] / 100
    imps = spend / 9.0 * 1000 * _mean_one_lognormal(rng, 0.12, len(meta))
    meta_ads = pd.DataFrame({
        "Day": meta["day"],
        "Campaign name": meta["cname"],
        "Campaign ID": meta["cid"],
        "Region": meta["state_code"].map(names),
        "Amount spent (USD)": np.round(spend, 2),
        "Impressions": np.round(imps).astype(np.int64),
        "Link clicks": np.round(imps * 0.012 * _mean_one_lognormal(rng, 0.1, len(meta))).astype(np.int64),
        "Purchases": np.round(meta["value"] / aov).astype(np.int64),
        "Purchases conversion value": np.round(meta["value"], 2),
    }).sort_values(["Day", "Campaign name", "Region"]).reset_index(drop=True)

    # ---------------- Shopify daily sales summary ----------------
    net = split_weekly_to_daily_cents(world["revenue"], DOW_SALES, rng,
                                      promo_daily=world["promo_daily"], promo_mult=1.0 + p["baseline"]["promo_lift"])
    promo_day = np.broadcast_to(world["promo_daily"], net.shape)
    disc_rate = np.where(promo_day, 0.18, 0.04) * _mean_one_lognormal(rng, 0.1, net.shape)
    ret_rate = 0.07 * _mean_one_lognormal(rng, 0.15, net.shape)
    gross = np.round(net / (1 - disc_rate - ret_rate)).astype(np.int64)
    discounts = -np.round(gross * disc_rate).astype(np.int64)
    returns = net - gross - discounts  # negative, makes net exact
    orders = np.round(gross / 100 / aov).astype(np.int64)
    shipping = np.round(orders * 4.95 * 0.4 * 100).astype(np.int64)
    taxes = np.round((gross + discounts) * 0.07).astype(np.int64)

    shop = _long(net, codes, days, "net")
    shop = pd.DataFrame({
        "day": shop["day"],
        "billing_region_code": shop["state_code"],
        "orders": orders.ravel(),
        "gross_sales": gross.ravel() / 100,
        "discounts": discounts.ravel() / 100,
        "returns": returns.ravel() / 100,
        "shipping": shipping.ravel() / 100,
        "taxes": taxes.ravel() / 100,
    })
    # Digital gift cards etc.: no billing region, not attributable to a state
    nr_gross = np.round(net.sum(axis=0) * m["shopify_no_region_share"] * _mean_one_lognormal(rng, 0.3, D)).astype(np.int64)
    noreg = pd.DataFrame({
        "day": days.strftime("%Y-%m-%d"), "billing_region_code": "",
        "orders": np.round(nr_gross / 100 / 50).astype(np.int64), "gross_sales": nr_gross / 100,
        "discounts": 0.0, "returns": 0.0, "shipping": 0.0, "taxes": 0.0,
    })
    shop = pd.concat([shop, noreg], ignore_index=True)
    shop["total_sales"] = np.round(shop["gross_sales"] + shop["discounts"] + shop["returns"]
                                   + shop["shipping"] + shop["taxes"], 2)
    shop = shop.sort_values(["day", "billing_region_code"]).reset_index(drop=True)

    # ---------------- Small reference files ----------------
    promo = world["promos"].copy()
    promo["start_date"] = pd.to_datetime(promo["start_date"]).dt.strftime("%m/%d/%Y")  # it's a Google Sheet
    promo["end_date"] = pd.to_datetime(promo["end_date"]).dt.strftime("%m/%d/%Y")

    ex = experiment_truth(world)
    design = pd.DataFrame({
        "state_code": codes,
        "test_group": np.where(world["is_treated"], "treatment", "control"),
        "channel_paused": np.where(world["is_treated"], "paid_search", ""),
        "test_start_date": ex["test_start"],
        "test_end_date": ex["test_end"],
    })
    census = pd.DataFrame(STATES, columns=["state_code", "state_name", "census_region", "population"])

    return {
        "google_ads_geo_daily": google_ads,
        "meta_ads_region_daily": meta_ads,
        "shopify_sales_daily": shop,
        "promo_calendar": promo,
        "geo_experiment_design": design,
        "census_state_population": census,
    }


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--out", default="data")
    args = ap.parse_args()

    params = copy.deepcopy(TRUE_PARAMS)
    if args.seed is not None:
        params["seed"] = args.seed

    world = simulate_world(params)
    truth_df, summary = truth_tables(world)
    raw = build_raw_exports(world, np.random.default_rng(params["seed"] + 1))

    out = Path(args.out)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    (out / "truth").mkdir(parents=True, exist_ok=True)
    for name, df in raw.items():
        df.to_csv(out / "raw" / f"{name}.csv", index=False)
    truth_df.to_csv(out / "truth" / "truth_geo_week.csv", index=False)
    with open(out / "truth" / "truth_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    print(f"Seed {params['seed']}: {len(truth_df):,} geo-weeks, total revenue ${summary['total_revenue']/1e6:,.1f}M "
          f"(baseline {summary['baseline_share_of_revenue']:.0%})")
    for c, v in summary["channels"].items():
        print(f"  {c:<13} spend ${v['total_spend']/1e6:6.1f}M  true ROI {v['true_roi']:.2f}  "
              f"share of revenue {v['share_of_revenue']:.1%}")
    e = summary["experiment"]
    print(f"  Experiment: {', '.join(e['treated_geos'])} | {e['test_start']} to {e['test_end']} | "
          f"withheld ${e['withheld_spend']/1e6:.2f}M, true iROAS {e['true_iroas_in_window']:.2f}")
    print("Raw exports:")
    for name, df in raw.items():
        print(f"  data/raw/{name}.csv  {len(df):,} rows")


if __name__ == "__main__":
    main()
