-- Geo holdout design, cleaned.
-- Grain: one row per state_code (all 50 states: treatment or control).
-- Treated states had channel_paused switched off between test_start_date and
-- test_end_date (inclusive); control states have no paused channel.

with source as (

    select * from {{ source('mmm_raw', 'geo_experiment_design') }}

),

renamed as (

    select
        state_code,
        test_group,
        test_group = 'treatment'                            as is_treated,
        nullif(trim(channel_paused), '')                    as channel_paused,
        cast(test_start_date as date)                       as test_start_date,
        cast(test_end_date as date)                         as test_end_date,
        _loaded_at

    from source

)

select * from renamed
