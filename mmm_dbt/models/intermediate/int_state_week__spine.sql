-- Every state x every Monday-start week in the modeling window.
-- Grain: one row per state_code x week_start (50 x 156 = 7,800 rows).
-- This is the backbone the weekly models left-join onto, so a state-week with
-- no ad rows (search paused, video off-flight) still exists and gets $0.

with weeks as (

    select week_start
    from unnest(
        generate_date_array(
            date('{{ var("mmm_start_week") }}'),
            date('{{ var("mmm_end_week") }}'),
            interval 1 week
        )
    ) as week_start

),

states as (

    select state_code, state_name, census_region, population
    from {{ ref('stg_census__state_population') }}

)

select
    states.state_code,
    states.state_name,
    states.census_region,
    states.population,
    weeks.week_start
from states
cross join weeks
