from datetime import date as ddate, datetime, time as dtime, timedelta
from decimal import Decimal

DEFAULT_FEED_TIME = dtime(6, 0)

# Four sessions spaced 4h apart starting at a pond's default feed time, splitting
# the day's total feed 25/30/30/15.
_SESSION_OFFSETS = (
    (timedelta(hours=0), Decimal("0.25")),
    (timedelta(hours=4), Decimal("0.30")),
    (timedelta(hours=8), Decimal("0.30")),
    (timedelta(hours=12), Decimal("0.15")),
)


def feeding_sessions_for(anchor_time: dtime) -> list[tuple[dtime, Decimal]]:
    anchor = datetime.combine(ddate.today(), anchor_time)
    return [((anchor + offset).time(), fraction) for offset, fraction in _SESSION_OFFSETS]
