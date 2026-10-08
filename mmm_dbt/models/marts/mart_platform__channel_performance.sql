-- A Mart used for reporting platform ROAS. 
-- Weekly spend and platform-reported conversion value by channel (Google Ads, Meta), with platform ROAS. Grain: channel × week. Used to compare platform-claimed ROAS against the MMM and the truth.”
-- Daily instead of weekly int table because weekly dropps platform_conversion_value and details about the campaign

with daily as (

    select *
    from {{ ref('int_ad_spend__daily_unioned') }}  
    where not is_unknown_geo -- Don't look at entries with missing states

),

totals as (

    select
        channel,
        week_start,
        sum(spend) as spend,
        sum(platform_conversion_value) as platform_conversion_value,
        safe_divide(sum(platform_conversion_value), sum(spend)) as platform_roas -- returns null instead of erroring when bottom num is 0
    from daily
    group by channel, week_start

)

select * from totals

