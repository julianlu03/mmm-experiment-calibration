-- Share of each week's days that fall inside a promo. National (same for every state).
-- Grain: one row per week_start (156 rows).

with promo_days as (

    -- one row per promo per day: a 5-day promo becomes 5 rows
    select
        promo_name,
        promo_day
    from {{ ref('stg_marketing__promo_calendar') }},
        unnest(generate_date_array(start_date, end_date)) as promo_day

),

weekly as (

    select
        date_trunc(promo_day, week(monday))     as week_start,
        count(distinct promo_day) / 7           as promo_share
    from promo_days
    group by week_start

),

weeks as (

    select distinct week_start
    from {{ ref('int_state_week__spine') }}

)

select
    weeks.week_start,
    coalesce(weekly.promo_share, 0)             as promo_share
from weeks
left join weekly
    on weeks.week_start = weekly.week_start