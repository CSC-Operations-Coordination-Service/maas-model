"""Contains ZuluDate class field"""

__all__ = ["ZuluDate"]

import datetime

import dateutil.parser
import dateutil.tz
from opensearchpy import Date


class ZuluDate(Date):
    """Custom field to serialize / serialize date in ZULU format"""

    name = "zuludate"

    _coerce = True

    @staticmethod
    def _format_zulu(value: datetime.datetime) -> str:
        """Render a datetime as a strict Zulu-format string.

        Built with plain string formatting rather than strftime: the
        previous implementation used non-standard width/zero-pad strftime
        directives (e.g. ``%04Y``) that are a glibc extension, they raise ValueError: Invalid format string
        on platforms whose C runtime doesn't support them (e.g. Windows).
        """
        return (
            f"{value.year:04d}-{value.month:02d}-{value.day:02d}T"
            f"{value.hour:02d}:{value.minute:02d}:{value.second:02d}."
            f"{value.microsecond // 1000:03d}Z"
        )

    def _serialize(self, data):
        """convert data to ZULU format"""

        # common case
        if isinstance(data, datetime.datetime):
            return self._format_zulu(data)

        # less common
        if isinstance(data, str) and data[-1] != "Z":
            data = dateutil.parser.parse(data).astimezone(dateutil.tz.UTC)
            return self._format_zulu(data)

        return data

    def _deserialize(self, data):
        if isinstance(data, str) and data[-1] == "Z":
            # optimization: fromisoformat is said to be fatest than other means
            # (strptime, etc)
            try:
                return datetime.datetime.fromisoformat(data[:-1] + "+00:00")
            except ValueError:
                # will try other strategy below
                pass

        data = super()._deserialize(data)
        # normalize UTC
        if data.tzinfo != datetime.UTC:
            data = data.astimezone(datetime.UTC)
        return data
