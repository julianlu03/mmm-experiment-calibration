-- Meta Ads geographic report, cleaned.
-- Grain: one row per date_day x campaign_id x state_name (state_name is null for unknown geo).
-- Staging rules: rename, cast, convert units, dedupe, categorize. No joins, no aggregation.

WITH source AS (

    select * from {{ source('mmm_raw', 'meta_ads_region_daily') }}

),

renamed as (

    select
        -- keys
        cast(day as date) as date_day,
        campaign_id,
        campaign_name,

        -- geo
        region AS state_name,
        false AS is_unknown_geo,
        'paid_social' AS channel, -- Mark all Meta ads as paid social
        -- metrics
        amount_spent_usd AS spend,
        impressions,
        link_clicks AS clicks,

        -- platform attribution (NOT used as KPI cause inflation)
        purchases AS platform_conversions,
        purchases_conversion_value AS platform_conversion_value,
        _loaded_at

    from source
)
SELECT * FROM renamed