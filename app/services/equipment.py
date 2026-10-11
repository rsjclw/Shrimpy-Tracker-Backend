"""Reading the equipment registry: a machine's status and where it is now come from its dated rows."""

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

# Only these change a machine's status; every other event (serviced, disinfected, note, ...) leaves it.
STATUS_FROM_EVENT = {"broken": "broken", "sent_for_repair": "in_repair", "repaired": "working", "retired": "retired"}

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def status_from_events(events: Iterable[Any]) -> str:
    """Working until an event says otherwise; the latest status event wins (by date, then when it was entered)."""
    status = "working"
    for event in sorted(events, key=lambda e: (e.date, e.created_at or _EPOCH)):
        status = STATUS_FROM_EVENT.get(event.kind, status)
    return status


def current_placement(placements: Iterable[Any]) -> Any | None:
    """The open placement (no end date); the latest one if, somehow, there are several."""
    open_rows = [p for p in placements if p.to_date is None]
    return max(open_rows, key=lambda p: p.from_date) if open_rows else None


def open_links(links: Iterable[Any]) -> list[Any]:
    return [link for link in links if link.to_date is None]
