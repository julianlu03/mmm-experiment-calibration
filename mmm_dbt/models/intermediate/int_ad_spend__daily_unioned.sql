-- Google Ads and Meta stacked into one daily table, with state codes and weeks.
-- Grain: one row per date_day x campaign_id x state (state is null for Google's unknown geo).
-- Kept at daily x campaign grain so both the MMM (spend) and the platform
-- performance mart (platform_* attribution) can build from it.

with google as (

    select
        date_day,
        state_name,
        is_unknown_geo,
        channel,
        campaign_id,
        campaign_name,
        spend,
        impressions,
        clicks,
        cast(platform_conversions as float64)   as platform_conversions,
        platform_conversion_value
    from {{ ref('stg_google_ads__geo_daily') }}

),

meta as (

    select
        date_day,
        state_name,
        false                                   as is_unknown_geo,  -- Meta always reports a state
        channel,
        campaign_id,
        campaign_name,
        spend,
        impressions,
        clicks,
        cast(platform_conversions as float64)   as platform_conversions,
        platform_conversion_value
    from {{ ref('stg_meta_ads__region_daily') }}

),

unioned as (

    select * from google
    union all
    select * from meta

),

states as (

    select state_code, state_name
    from {{ ref('stg_census__state_population') }}

)

select
    unioned.date_day,
    date_trunc(unioned.date_day, week(monday))  as week_start,  -- BigQuery's plain WEEK starts on Sunday
    states.state_code,
    unioned.is_unknown_geo,
    unioned.channel,
    unioned.campaign_id,
    unioned.campaign_name,
    unioned.spend,
    unioned.impressions,
    unioned.clicks,
    unioned.platform_conversions,
    unioned.platform_conversion_value
from unioned
left join states
    on unioned.state_name = states.state_name
