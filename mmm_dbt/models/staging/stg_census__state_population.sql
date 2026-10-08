-- 2020 Census state populations. Reference list of valid states and the
-- denominator for per-capita metrics.
-- Grain: one row per state_code.

with source as (

    select * from {{ source('mmm_raw', 'census_state_population') }}

),

renamed as (

    select
        state_code,
        state_name,
        census_region,
        population,
        _loaded_at

    from source

)

select * from renamed
