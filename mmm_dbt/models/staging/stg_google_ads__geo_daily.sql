-- Google Ads geographic report, cleaned.
-- Grain: one row per date_day x campaign_id x state_name (state_name is null for unknown geo).
-- Staging rules: rename, cast, convert units, dedupe, categorize. No joins, no aggregation.

with source as (

    select * from {{ source('mmm_raw', 'google_ads_geo_daily') }}

),

renamed as (

    select
        -- keys
        cast(segments_date as date)                         as date_day,
        campaign_id,
        campaign_name,

        -- geo: Google reports "California, United States"; "Unknown" means
        -- Google couldn't locate the user. Kept (flagged) so spend reconciles.
        case
            when geo_target_state = 'Unknown' then null
            else trim(replace(geo_target_state, ', United States', ''))
        end                                                 as state_name,
        geo_target_state = 'Unknown'                        as is_unknown_geo,

        -- one export, two channels
        case campaign_advertising_channel_type
            when 'SEARCH' then 'paid_search'
            when 'VIDEO'  then 'online_video'
        end                                                 as channel,

        -- metrics: cost arrives in micros (1,000,000 micros = $1)
        metrics_cost_micros / 1000000                       as spend,
        metrics_impressions                                 as impressions,
        metrics_clicks                                      as clicks,

        -- platform attribution, NOT incrementality: never use as the KPI
        metrics_conversions                                 as platform_conversions,
        metrics_conversions_value                           as platform_conversion_value,

        _loaded_at

    from source

),

numbered as (

    -- An overlapping re-pull duplicated a week of rows. Number the copies of
    -- each grain, newest load first, then keep only the first copy.
    select
        *,
        row_number() over (
            partition by date_day, campaign_id, state_name
            order by _loaded_at desc
        ) as row_num
    from renamed

),

deduplicated as (

    select * except (row_num)
    from numbered
    where row_num = 1

)

select * from deduplicated
