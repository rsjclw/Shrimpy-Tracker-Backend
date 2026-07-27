"""Moon phase and molt windows, derived from a date alone.

Shrimp molt around the two syzygies (new moon and full moon), and alkalinity,
calcium and magnesium demand spikes with them. Nothing here is stored - it is
computed on demand like every other metric.

Windows are defined by distance to the exact syzygy, NOT by an illumination
threshold. Illumination is a cosine at its turning point near both new and full
moon, so it barely moves there - about 4 points across a whole day near full,
against 12 points/day near quarter. Thresholding it is ill-conditioned: nudging
0.93 to 0.90 slides the window edge by most of a day. `days_to_full` and
`days_to_new` are linear and well-conditioned. Illumination is display only.
"""
from dataclasses import dataclass
from datetime import date as ddate
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import ephem

# Window width either side of the exact syzygy, in days. Deliberately
# asymmetric: molting builds through the run-up and tails off afterwards, so
# the window reaches further before the peak than after it.
FULL_DAYS_BEFORE = 4.0
FULL_DAYS_AFTER = 2.0
NEW_DAYS_BEFORE = 4.0
NEW_DAYS_AFTER = 2.0

# Minerals need to be up before molting starts, not once it is underway, so the
# run-up is flagged from the moment the window opens.
LEAD_DAYS = 4.0

FULL = "full"
NEW = "new"


@dataclass(frozen=True)
class LunarDay:
    illumination: float  # 0.0-1.0, display only - see module docstring
    waxing: bool
    days_to_full: float  # signed; negative means the full moon has passed
    days_to_new: float
    window: str | None  # "full" | "new" | None
    is_peak: bool  # the exact syzygy falls on this date
    alert: str | None  # "full" | "new" while approaching, within LEAD_DAYS


def _zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _signed_days_to_nearest(t: ephem.Date, previous, following) -> float:
    """Days to the closest syzygy of one kind; negative once it has passed."""
    ahead = float(following(t) - t)
    behind = float(t - previous(t))
    return ahead if ahead <= behind else -behind


def _in_window(days_to: float, before: float, after: float) -> bool:
    """`days_to` counts down to the syzygy, then goes negative past it."""
    return -after <= days_to <= before


def lunar_day(day: ddate, tz: str) -> LunarDay:
    """Moon state for a calendar date in a given IANA timezone."""
    # Local noon is a neutral point inside the date. The window classification
    # is insensitive to the exact hour, so this avoids arbitrary edge effects.
    local_noon = datetime.combine(day, time(12, 0), _zone(tz))
    # ephem works in UTC and wants naive datetimes - passing an aware one, or a
    # local one, silently shifts every result.
    t = ephem.Date(local_noon.astimezone(timezone.utc).replace(tzinfo=None))

    illumination = ephem.Moon(t).phase / 100.0
    days_to_full = _signed_days_to_nearest(
        t, ephem.previous_full_moon, ephem.next_full_moon
    )
    days_to_new = _signed_days_to_nearest(
        t, ephem.previous_new_moon, ephem.next_new_moon
    )
    waxing = float(ephem.next_full_moon(t) - t) < float(ephem.next_new_moon(t) - t)

    window: str | None = None
    if _in_window(days_to_full, FULL_DAYS_BEFORE, FULL_DAYS_AFTER):
        window = FULL
    elif _in_window(days_to_new, NEW_DAYS_BEFORE, NEW_DAYS_AFTER):
        window = NEW

    # Local noon is the reference, so the syzygy lands on this date when it is
    # within 12 h of it.
    is_peak = abs(days_to_full) <= 0.5 or abs(days_to_new) <= 0.5

    # Only the run-up is actionable; the tail of a window is not a cue to dose.
    alert: str | None = None
    if 0 < days_to_full <= LEAD_DAYS:
        alert = FULL
    elif 0 < days_to_new <= LEAD_DAYS:
        alert = NEW

    return LunarDay(
        illumination=illumination,
        waxing=waxing,
        days_to_full=days_to_full,
        days_to_new=days_to_new,
        window=window,
        is_peak=is_peak,
        alert=alert,
    )
