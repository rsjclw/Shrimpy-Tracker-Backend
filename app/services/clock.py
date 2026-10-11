from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import settings


def farm_today() -> date:
    """Today on the farm's clock (WIB by default).

    Log dates and feed times are stored as farm-local values, so "today" must
    be the farm's date too: the UTC date lags a day until 07:00 WIB.
    """
    return farm_now().date()


def farm_now() -> datetime:
    """Now on the farm's clock, without a zone, to compare with a log date and feed time."""
    return datetime.now(ZoneInfo(settings.default_timezone)).replace(tzinfo=None)
