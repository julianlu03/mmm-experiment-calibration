-- Shopify sales dataset, cleaned. Source of truth for revenue (the MMM's KPI). Grain: date_day x state_code. No duplicates, so no dedupe step."Source of truth for revenue (the MMM's KPI). Grain: date_day x state_code. No duplicates, so no dedupe step."
-- Grain: one row per date_day x state_name
-- Staging rules: rename, cast, convert units, dedupe, categorize. No joins, no aggregation.

with source as (

    select * from {{source('mmm_raw', 'shopify_sales_daily') }}

),

renamed as (

    select
        -- keys
        cast(day as date) as date_day,

        -- geo
        nullif(trim(billing_region_code), '')           as state_code,
        nullif(trim(billing_region_code), '') is null   as is_unknown_geo,

        -- metrics
        orders,
        gross_sales,
        discounts,
        returns,
        -- net sales
        shipping,
        taxes,
        total_sales,
        gross_sales + discounts + returns as net_sales,

        _loaded_at
    
    from source
)  
select * from renamed