"""
Ground-truth parameters for the simulated DTC e-commerce brand.

Everything the MMM is supposed to recover lives here. The modeling code (Meridian,
DiD, synthetic control) must never import this file; only the evaluation step
compares model output against it.

Conventions
-----------
- Time grain: weeks starting Monday. 156 weeks from 2023-01-02 to 2025-12-28.
- Geo grain: the 50 US states (2020 Census populations, rounded).
- KPI: net sales (revenue after discounts and returns, before shipping and tax).
- ROI: incremental net sales per $1 of media spend. 1.0 = breakeven before margin.
- Hill EC50 is expressed relative to each geo-channel's average weekly spend, so
  EC50 = 1.0 means a channel is at half its maximum effect at its typical spend.
"""

TRUE_PARAMS = {
    "seed": 42,
    "start_date": "2023-01-02",  # a Monday
    "n_weeks": 156,

    # ---- Baseline demand (what sales would be with zero paid media) ----
    "baseline": {
        "revenue_per_capita_weekly": 0.03,  # $ per resident per week (~$470M/yr nationally)
        "geo_level_sd": 0.10,               # per-capita demand differs by state (lognormal sd)
        "trend_mean": 0.0008,               # weekly log growth (~4%/yr on average)
        "trend_sd": 0.0010,                 # states grow at different rates
        "noise_sd": 0.02,                   # iid weekly noise
        "promo_lift": 0.25,                 # +25% baseline demand on promo days
    },

    # ---- FAILURE MODE 1: hidden demand that also drives search spend ----
    # Geo-level AR(1) demand shocks. They raise baseline sales AND (through the
    # search auction / budget pacing) raise paid search spend. The model never
    # sees this variable, so it credits some organic demand to search.
    "demand_shock": {
        "ar1": 0.7,
        "sd": 0.03,  # stationary sd of the log shock (+/-3% swings in baseline demand)
    },

    # ---- Media channels ----
    "channels": {
        "paid_search": {
            "spend_share": 0.06,       # avg spend as share of baseline revenue
            "roi": 1.8,
            "adstock_decay": 0.15,     # fast: most effect lands the same week
            "hill_ec50": 1.0,
            "hill_slope": 1.5,
            "demand_elasticity": 1.25,  # search spend moves 1.25% per 1% hidden demand (endogeneity)
            "season_exponent": 1.0,    # search volume follows seasonality fully
            "promo_boost": 0.5,
            "weekly_noise_sd": 0.10,
        },
        "paid_social": {
            "spend_share": 0.04,
            "roi": 1.4,
            "adstock_decay": 0.40,
            "hill_ec50": 1.0,
            "hill_slope": 1.2,
            "demand_elasticity": 0.0,  # exogenous: budget set in advance
            "season_exponent": 0.6,
            "promo_boost": 0.3,
            "weekly_noise_sd": 0.35,   # geo-level budget/delivery swings give the MMM signal to learn from
        },
        "online_video": {
            "spend_share": 0.025,
            "roi": 0.8,                # below breakeven: the reallocation finding
            "adstock_decay": 0.65,     # slow: brand-style carryover
            "hill_ec50": 1.2,
            "hill_slope": 1.0,
            "demand_elasticity": 0.0,
            "season_exponent": 0.5,
            "promo_boost": 0.0,
            "weekly_noise_sd": 0.15,
            "flighting": {"on_weeks": (4, 8), "off_weeks": (2, 6)},  # runs in bursts, staggered by Census region
        },
    },
    "adstock_max_lag": 8,  # matches Meridian's default max_lag

    # ---- FAILURE MODE 2: geo experiment with non-parallel trends ----
    # Paid search is paused in 10 treated states for 8 weeks. Treated states are
    # picked from a "growth markets" list (fast-trending, mid-sized states), the
    # way test markets often get chosen by convenience. Their faster growth
    # violates DiD's parallel-trends assumption. Other fast-growing states remain
    # in the donor pool, so synthetic control can still match them.
    # The test sits mid-sample on purpose: geo fixed effects in an MMM absorb a
    # state's average level, so a pause in fast-growing states near the END of
    # the sample would also bias the MMM (zero spend lining up with
    # above-average sales). Mid-sample keeps the two failure modes separate.
    "experiment": {
        "channel": "paid_search",
        "n_treated_geos": 10,
        "start_week": 74,            # 2024-06-03, mid-sample (see note above)
        "duration_weeks": 8,         # through 2024-07-28
        "candidate_pop_range": (1.5e6, 12e6),
        "candidate_pool_size": 18,   # treated geos drawn from the 18 fastest-growing mid-sized states
    },

    # ---- Platform-reported conversions (attribution, not incrementality) ----
    # Ad platforms claim credit for sales that would have happened anyway and
    # double count across each other. Reported value = true incremental value x factor,
    # but only in weeks the channel is live: carryover sales after a flight ends
    # fall outside attribution windows, so slow-decay channels get under-credited.
    "platform_overreport": {
        "paid_search": 1.6,
        "paid_social": 1.3,
        "online_video": 1.1,
    },

    # ---- Raw export messiness (handled in dbt, NOT a causal problem) ----
    "messiness": {
        "google_ads_duplicate_dates": ("2024-03-04", "2024-03-10"),  # overlapping re-pull
        "google_ads_unknown_geo_share": 0.01,  # spend Google couldn't geo-locate
        "shopify_no_region_share": 0.005,       # gift cards / digital orders with no billing region
        "brand_search_share": 0.30,
        "meta_retargeting_share": 0.30,
        "avg_order_value": 85.0,
    },
}

# 50 states: code, name, Census region, population (2020 Census, rounded)
STATES = [
    ("AL", "Alabama", "South", 5_024_000), ("AK", "Alaska", "West", 733_000),
    ("AZ", "Arizona", "West", 7_152_000), ("AR", "Arkansas", "South", 3_012_000),
    ("CA", "California", "West", 39_538_000), ("CO", "Colorado", "West", 5_774_000),
    ("CT", "Connecticut", "Northeast", 3_606_000), ("DE", "Delaware", "South", 990_000),
    ("FL", "Florida", "South", 21_538_000), ("GA", "Georgia", "South", 10_712_000),
    ("HI", "Hawaii", "West", 1_455_000), ("ID", "Idaho", "West", 1_839_000),
    ("IL", "Illinois", "Midwest", 12_813_000), ("IN", "Indiana", "Midwest", 6_786_000),
    ("IA", "Iowa", "Midwest", 3_190_000), ("KS", "Kansas", "Midwest", 2_938_000),
    ("KY", "Kentucky", "South", 4_506_000), ("LA", "Louisiana", "South", 4_658_000),
    ("ME", "Maine", "Northeast", 1_362_000), ("MD", "Maryland", "South", 6_177_000),
    ("MA", "Massachusetts", "Northeast", 7_030_000), ("MI", "Michigan", "Midwest", 10_077_000),
    ("MN", "Minnesota", "Midwest", 5_706_000), ("MS", "Mississippi", "South", 2_961_000),
    ("MO", "Missouri", "Midwest", 6_155_000), ("MT", "Montana", "West", 1_084_000),
    ("NE", "Nebraska", "Midwest", 1_962_000), ("NV", "Nevada", "West", 3_105_000),
    ("NH", "New Hampshire", "Northeast", 1_378_000), ("NJ", "New Jersey", "Northeast", 9_289_000),
    ("NM", "New Mexico", "West", 2_118_000), ("NY", "New York", "Northeast", 20_201_000),
    ("NC", "North Carolina", "South", 10_439_000), ("ND", "North Dakota", "Midwest", 779_000),
    ("OH", "Ohio", "Midwest", 11_799_000), ("OK", "Oklahoma", "South", 3_959_000),
    ("OR", "Oregon", "West", 4_237_000), ("PA", "Pennsylvania", "Northeast", 13_003_000),
    ("RI", "Rhode Island", "Northeast", 1_097_000), ("SC", "South Carolina", "South", 5_119_000),
    ("SD", "South Dakota", "Midwest", 887_000), ("TN", "Tennessee", "South", 6_911_000),
    ("TX", "Texas", "South", 29_146_000), ("UT", "Utah", "West", 3_272_000),
    ("VT", "Vermont", "Northeast", 643_000), ("VA", "Virginia", "South", 8_631_000),
    ("WA", "Washington", "West", 7_705_000), ("WV", "West Virginia", "South", 1_794_000),
    ("WI", "Wisconsin", "Midwest", 5_894_000), ("WY", "Wyoming", "West", 577_000),
]

CHANNELS = list(TRUE_PARAMS["channels"].keys())
