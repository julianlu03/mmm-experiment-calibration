-- The mart must have exactly one row per state per week:
-- (number of states) x (number of Monday weeks in the window).
-- Returns a row (= test fails) if the count is off.

with expected as (

    select
        (select count(*) from {{ ref('stg_census__state_population') }})
        * (div(date_diff(date('{{ var("mmm_end_week") }}'), date('{{ var("mmm_start_week") }}'), day), 7) + 1)
            as expected_rows

),

actual as (

    select count(*) as actual_rows
    from {{ ref('mart_mmm__state_week') }}

)

select expected_rows, actual_rows
from expected
cross join actual
where expected_rows != actual_rows
