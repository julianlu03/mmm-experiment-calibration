-- Totals in the mart must match the cleaned staging data (state-attributed rows only).
-- Catches rows lost or duplicated by the week rollup or the joins.
-- Returns one row per metric that is off by more than a cent.

with mart as (

    select
        sum(net_sales)           as net_sales,
        sum(spend_paid_search)   as spend_paid_search,
        sum(spend_paid_social)   as spend_paid_social,
        sum(spend_online_video)  as spend_online_video
    from {{ ref('mart_mmm__state_week') }}

),

sales as (

    select sum(net_sales) as net_sales
    from {{ ref('stg_shopify__sales_daily') }}
    where not is_unknown_geo
      and date_day between date('{{ var("mmm_start_week") }}')
                       and date_add(date('{{ var("mmm_end_week") }}'), interval 6 day)

),

spend as (

    select
        sum(if(channel = 'paid_search',  spend, 0))  as spend_paid_search,
        sum(if(channel = 'paid_social',  spend, 0))  as spend_paid_social,
        sum(if(channel = 'online_video', spend, 0))  as spend_online_video
    from {{ ref('int_ad_spend__daily_unioned') }}
    where not is_unknown_geo
      and date_day between date('{{ var("mmm_start_week") }}')
                       and date_add(date('{{ var("mmm_end_week") }}'), interval 6 day)

),

comparison as (

    select 'net_sales' as metric, mart.net_sales as mart_total, sales.net_sales as source_total
    from mart cross join sales
    union all
    select 'spend_paid_search', mart.spend_paid_search, spend.spend_paid_search from mart cross join spend
    union all
    select 'spend_paid_social', mart.spend_paid_social, spend.spend_paid_social from mart cross join spend
    union all
    select 'spend_online_video', mart.spend_online_video, spend.spend_online_video from mart cross join spend

)

select *
from comparison
where abs(mart_total - source_total) > 0.01
