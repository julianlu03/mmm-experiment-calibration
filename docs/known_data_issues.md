# Known data issues in the raw exports

**Spoiler warning.** These are the quirks deliberately planted in `data/raw/`. For the best
learning experience, profile the raw tables in BigQuery yourself first (Day 2), write down
what you find, then compare against this list.

None of these are causal problems. They are the data engineering layer that dbt fixes. The
causal problems (endogeneity, non-parallel trends) can't be cleaned away and are handled in
the analysis.

| # | Source | Issue | Why it matters | Suggested dbt handling | Test that catches it |
|---|---|---|---|---|---|
| 1 | Google Ads | Exact duplicate rows for 2024-03-04 to 2024-03-10 (an overlapping re-pull) | Inflates spend that week, and the MMM reads it as a spend spike | Dedupe in staging (`qualify row_number() over (...) = 1` or `select distinct`) | `unique` on (date, campaign_id, geo) |
| 2 | Google Ads | Cost is in micros (`metrics_cost_micros`) | Spend off by 1,000,000x | `metrics_cost_micros / 1e6` | Range test on weekly spend |
| 3 | Google Ads | `geo_target_state = 'Unknown'` rows (~1% of spend) | Real money, but no state to assign it to | Exclude from the geo mart, keep a separate total so the gap is visible | Reconciliation: mart spend + unknown = raw spend |
| 4 | All | Geo naming differs: `California, United States` vs `California` vs `CA` | Joins silently drop states | Map everything to `state_code` via the census table | `relationships` test to the census seed; mart has 50 states |
| 5 | Google Ads | Search and YouTube share one export | Two channels in one table | Map `campaign_advertising_channel_type` (SEARCH, VIDEO) to channel | `accepted_values` on channel |
| 6 | Google Ads, Meta | Zero-spend days are not exported (video off-flights, search paused in test states) | Missing rows become nulls or dropped weeks instead of $0 | Build a date spine x state x channel and `coalesce(spend, 0)` | Mart row count = 50 states x 156 weeks |
| 7 | All | Daily grain; MMM needs Monday-start weeks | BigQuery's `DATE_TRUNC(d, WEEK)` defaults to **Sunday** weeks | `DATE_TRUNC(d, WEEK(MONDAY))` | Every `week_start` is a Monday |
| 8 | All | Dates land as strings; the promo calendar uses `MM/DD/YYYY` | Type errors, or wrong parsing | `PARSE_DATE('%m/%d/%Y', ...)` for promos, `DATE(...)` elsewhere | `not_null` after casting |
| 9 | Shopify | `discounts` and `returns` are negative; `total_sales` includes shipping and tax | Wrong KPI if you use `total_sales` or subtract negatives | KPI = `gross_sales + discounts + returns` (net sales) | Net sales <= gross sales |
| 10 | Shopify | Rows with no `billing_region_code` (gift cards, ~0.5% of sales) | Not attributable to a state | Exclude from the geo KPI, report separately | Reconciliation test |
| 11 | Promo calendar | Includes 2023-01-01, before the data starts; promos span partial weeks | Off-range dates; binary weekly flags lose information | Expand to promo days, aggregate to `promo_days / 7` per week | Values between 0 and 1 |
| 12 | Google Ads, Meta | `conversions_value` is platform attribution, not incrementality | Using it as the KPI or as "truth" defeats the project | Keep it in a separate campaign-performance mart for the platform ROAS comparison | n/a, it's a modeling decision |
| 13 | Meta | Original headers have spaces and parentheses (`Amount spent (USD)`) | Not valid BigQuery column names | The loader snake_cases them, like Fivetran/Airbyte do | n/a |

## What a clean mart should look like

One row per `state_code` x `week_start` (7,800 rows), with population, net sales, spend per
channel, and promo share. If your mart reconciles to the raw totals (minus the unknown-geo
and no-region rows), it will match the truth table to the cent. `validate_simulation.py`
section 1 runs that reconciliation in pandas as a simulator self-test; avoid reading it
before you write the dbt version.
