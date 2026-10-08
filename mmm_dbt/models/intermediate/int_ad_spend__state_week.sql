-- Weekly media spend by state, one column per channel, zero-filled.
-- Grain: one row per state_code x week_start (7,800 rows).
-- Unknown-geo Google spend is excluded: it can't be assigned to a state.

with daily as (

    select *
    from {{ ref('int_ad_spend__daily_unioned') }}
    where not is_unknown_geo

),

weekly as (

    select
        state_code,
        week_start,
        sum(if(channel = 'paid_search',  spend, 0))  as spend_paid_search,
        sum(if(channel = 'paid_social',  spend, 0))  as spend_paid_social,
        sum(if(channel = 'online_video', spend, 0))  as spend_online_video
    from daily
    group by state_code, week_start

),

spine as (

    select state_code, week_start
    from {{ ref('int_state_week__spine') }}

)

-- Left join from the spine: weeks with no ad rows at all come back as nulls,
-- which coalesce turns into $0. That's how the search pause stays in the data.
select
    spine.state_code,
    spine.week_start,
    coalesce(weekly.spend_paid_search,  0)  as spend_paid_search,
    coalesce(weekly.spend_paid_social,  0)  as spend_paid_social,
    coalesce(weekly.spend_online_video, 0)  as spend_online_video
from spine
left join weekly
    on  spine.state_code = weekly.state_code
    and spine.week_start = weekly.week_start
