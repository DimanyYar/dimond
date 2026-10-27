"""Time zones people pick for themselves: every time on their pages shows in theirs (UTC is
stored, CLAUDE.md). Names are IANA ones, e.g. Europe/Moscow, as browsers report them."""

from datetime import UTC, date, datetime, time
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones

from django.core.exceptions import ValidationError

# Region/City names only: legacy aliases such as "EST5EDT", "Etc/GMT+3" (whose sign is the
# reverse of what it reads) and "SystemV/..." would only confuse the list.
REGIONS = (
    "Africa/",
    "America/",
    "Antarctica/",
    "Asia/",
    "Atlantic/",
    "Australia/",
    "Europe/",
    "Indian/",
    "Pacific/",
)


def time_zone_choices() -> list[tuple[str, str]]:
    """("Europe/Moscow", "UTC+03:00 · Europe/Moscow"), from the farthest west to the farthest
    east, so a person finds theirs next to cities they know. The offsets are today's: summer
    time moves some of them by an hour, so the list is built again each day."""
    return _choices_on(datetime.now(UTC).date())


@lru_cache(maxsize=1)
def _choices_on(day: date) -> list[tuple[str, str]]:
    at = datetime.combine(day, time(12), tzinfo=UTC)
    names = [name for name in available_timezones() if name.startswith(REGIONS)] + ["UTC"]
    zones = []
    for name in names:
        offset = at.astimezone(ZoneInfo(name)).utcoffset()
        assert offset is not None  # every IANA zone has an offset
        minutes = int(offset.total_seconds()) // 60
        sign = "+" if minutes >= 0 else "−"
        label = f"UTC{sign}{abs(minutes) // 60:02}:{abs(minutes) % 60:02} · {name}"
        zones.append((minutes, name, label))
    return [(name, label) for _, name, label in sorted(zones)]


def validate_time_zone(value: str) -> None:
    if value and value not in dict(time_zone_choices()):
        raise ValidationError("Pick a time zone from the list.")
