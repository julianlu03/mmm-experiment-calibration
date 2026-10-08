-- Marketing team's promo calendar (exported from a Google Sheet), cleaned.
-- Grain: one row per promo_name x start_date.
-- Dates arrive as MM/DD/YYYY strings. Promos outside the data window
-- (e.g. 2023-01-01) are kept here; the weekly join in intermediate ignores them.

with source as (

    select * from {{ source('mmm_raw', 'promo_calendar') }}

),

renamed as (

    select
        trim(promo_name)                                    as promo_name,
        parse_date('%m/%d/%Y', start_date)                  as start_date,
        parse_date('%m/%d/%Y', end_date)                    as end_date,
        discount_pct,
        _loaded_at

    from source

),

final as (

    select
        promo_name,
        start_date,
        end_date,
        date_diff(end_date, start_date, day) + 1            as promo_length_days,  -- end date is inclusive
        discount_pct,
        _loaded_at

    from renamed

)

select * from final
