-- The MMM input table: one row per state-week with the KPI, media spend,
-- controls, and geo-experiment flags. Read directly by Meridian (Day 3) and
-- by the DiD / synthetic control analysis (Day 4).
-- Grain: one row per state_code x week_start (50 x 156 = 7,800 rows).

with spine as (

    select state_code, state_name, census_region, population, week_start
    from {{ ref('int_state_week__spine') }}

),

sales as (

    select state_code, week_start, net_sales
    from {{ ref('int_sales__state_week') }}

),

spend as (

    select state_code, week_start, spend_paid_search, spend_paid_social, spend_online_video
    from {{ ref('int_ad_spend__state_week') }}

),

promo as (

    select week_start, promo_share
    from {{ ref('int_promo__week') }}

),

experiment as (

    select state_code, is_treated, test_start_date, test_end_date
    from {{ ref('stg_experiment__geo_design') }}

)

select
    -- keys and geo attributes
    spine.state_code,
    spine.state_name,
    spine.census_region,
    spine.population,
    spine.week_start,

    -- KPI
    sales.net_sales,

    -- media
    spend.spend_paid_search,
    spend.spend_paid_social,
    spend.spend_online_video,

    -- controls
    promo.promo_share,

    -- geo experiment
    experiment.is_treated                                       as is_treated_geo,
    spine.week_start between experiment.test_start_date
                         and experiment.test_end_date           as is_test_period,
    experiment.is_treated
        and spine.week_start between experiment.test_start_date
                                 and experiment.test_end_date   as is_search_paused

from spine
left join sales
    on  spine.state_code = sales.state_code
    and spine.week_start = sales.week_start
left join spend
    on  spine.state_code = spend.state_code
    and spine.week_start = spend.week_start
left join promo
    on  spine.week_start = promo.week_start
left join experiment
    on  spine.state_code = experiment.state_code
