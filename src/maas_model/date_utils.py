"""Functions about ZULU date management"""

import datetime

import dateutil.parser

__all__ = [
    "datetime_to_zulu",
    "datestr_to_zulu",
    "datestr_to_utc_datetime",
    "parse_api_date",
]


def parse_api_date(
    value: str | datetime.datetime | None,
) -> datetime.datetime | None:
    """Parse a date as sent by the API into a UTC datetime.

    '' -> None; 'dd/mm/YYYY' (UK, day first) -> UTC datetime; anything else
    is parsed as ISO. Naive values are taken as UTC, not local time.
    The day-first format is explicit: dateutil's dayfirst=True would also
    flip ISO dates ('2014-10-01' -> 10 January).
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        parsed = value
    else:
        try:
            parsed = datetime.datetime.strptime(value, "%d/%m/%Y")
        except ValueError:
            parsed = dateutil.parser.isoparse(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.UTC)
    return parsed.astimezone(datetime.UTC)


def datetime_to_zulu(datetime_object: datetime.datetime | None) -> str | None:
    """Format a datetime object to ZULU format

    Args:
        datetime_object (datetime.datetime): datetime to format

    Returns:
        str: zulu formatted string or None if datetime_object is None

    Built with plain string formatting rather than strftime: the
    previous implementation used non-standard width/zero-pad strftime
    directives (e.g. ``%04Y``) that are a glibc extension, they raise ValueError: Invalid format string
    on platforms whose C runtime doesn't support them (e.g. Windows).
    """
    if datetime_object is None:
        return None
    
    return (
        f"{datetime_object.year:04d}-{datetime_object.month:02d}-"
        f"{datetime_object.day:02d}T{datetime_object.hour:02d}:"
        f"{datetime_object.minute:02d}:{datetime_object.second:02d}."
        f"{datetime_object.microsecond // 1000:03d}Z"
    )


def datestr_to_zulu(date_str: str | None) -> str | None:
    """Convert a iso datetime string to ZULU format compatible with MAAS

    Args:
        date_str (str): an iso string

    Returns:
        str: zulu formatted string or None if date_str is None
    """
    if date_str is None:
        return None
    return datetime_to_zulu(datestr_to_utc_datetime(date_str))


def datestr_to_utc_datetime(date_str: str | None) -> datetime.datetime | None:
    """Convert a iso datetime string to utc datetime object

    Args:
        date_str (str): an iso string

    Returns:
        datetime.datetime: utc datetime object or None if date_str is None
    """
    if date_str is None:
        return None
    return dateutil.parser.isoparse(date_str).astimezone(dateutil.tz.UTC)
