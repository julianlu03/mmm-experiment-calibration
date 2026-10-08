-- Business-logic check on the geo experiment: wherever the design says paid
-- search was paused, the mart must show $0 search spend. A failure means the
-- experiment didn't run as designed, or the week alignment is off.

select state_code, week_start, spend_paid_search
from {{ ref('mart_mmm__state_week') }}
where is_search_paused
  and spend_paid_search > 0
