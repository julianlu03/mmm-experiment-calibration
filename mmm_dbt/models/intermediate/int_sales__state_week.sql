-- Shopify net sales by state-week
-- Reference the shopify view
-- aggregate by Monday start week and state
-- Join onto spine 

with daily as (

    select *
    from {{ ref('stg_shopify__sales_daily')}}
    where not is_unknown_geo
),

weekly as (
    select
        state_code,
        date_trunc(date_day, week(monday))  as week_start,
        sum(net_sales)                      as net_sales
    from daily
    group by state_code, week_start
),

spine as (

    select state_code, week_start
    from {{ ref('int_state_week__spine') }}

)

select
    spine.state_code,
    spine.week_start,
    coalesce(weekly.net_sales, 0)           as net_sales
from spine
left join weekly
    on  spine.state_code = weekly.state_code
    and spine.week_start = weekly.week_start