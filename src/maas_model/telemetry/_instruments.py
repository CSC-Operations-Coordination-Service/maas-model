"""
Lazy metric instruments.

Counter and Histogram are proxies: they resolve the real OpenTelemetry
instrument from the meter on first use, and again whenever the state
generation changes (configure() or reset()), so they can be created at import
time, before configure(). They do nothing while telemetry is disabled and
never raise at record time.
"""

import logging
import threading
from typing import Any, Mapping, Optional, Sequence

from maas_model.telemetry import _state, semconv

LOGGER = logging.getLogger("Telemetry")

# distinct attribute sets per instrument before values collapse to "other"
MAX_ATTRIBUTE_SETS = 500

OTHER = "other"


class _Instrument:
    """what Counter and Histogram share"""

    def __init__(self, name: str, unit: str, description: str):
        self.name = name
        self.unit = unit
        self.description = description
        self._generation = -1
        self._real = None
        self._seen = set()
        self._warned_keys = set()
        self._warned_cardinality = False
        self._lock = threading.Lock()

    def _create(self, meter):
        raise NotImplementedError

    def _resolve(self):
        """the real instrument, or None while disabled"""
        state = _state.STATE
        if self._generation != state.generation:
            with self._lock:
                if self._generation != state.generation:
                    self._real = (
                        self._create(state.meter)
                        if state.enabled and state.meter is not None
                        else None
                    )
                    self._seen = set()
                    self._warned_keys = set()
                    self._warned_cardinality = False
                    self._generation = state.generation
        return self._real

    def _attributes(self, attributes: Optional[Mapping[str, Any]]) -> dict:
        clean = {}
        for key, value in (attributes or {}).items():
            if semconv.ID_LABEL_DENYLIST.match(key):
                if key not in self._warned_keys:
                    self._warned_keys.add(key)
                    LOGGER.warning(
                        "metric %s: label %r looks like an identifier, dropped",
                        self.name,
                        key,
                    )
                continue
            clean[key] = "unknown" if value is None else str(value)

        key = frozenset(clean.items())
        if key not in self._seen:
            if len(self._seen) >= MAX_ATTRIBUTE_SETS:
                if not self._warned_cardinality:
                    self._warned_cardinality = True
                    LOGGER.warning(
                        "metric %s: more than %d attribute sets, "
                        "further label values become %r",
                        self.name,
                        MAX_ATTRIBUTE_SETS,
                        OTHER,
                    )
                clean = {k: OTHER for k in clean}
            else:
                self._seen.add(key)

        clean.update(_state.STATE.base_attributes)
        return clean

    def _record(self, method: str, value, attributes, context) -> None:
        try:
            real = self._resolve()
            if real is None:
                return
            getattr(real, method)(
                value, self._attributes(attributes), context=context
            )
        except Exception:  # pylint: disable=broad-except
            LOGGER.debug("metric %s: recording failed", self.name, exc_info=True)


class Counter(_Instrument):
    """monotonic counter"""

    def _create(self, meter):
        return meter.create_counter(
            self.name, unit=self.unit, description=self.description
        )

    def add(
        self,
        amount: float,
        attributes: Optional[Mapping[str, Any]] = None,
        *,
        context=None,
    ) -> None:
        """add amount, a no-op while telemetry is disabled"""
        self._record("add", amount, attributes, context)


class Histogram(_Instrument):
    """histogram with explicit bucket boundaries"""

    def __init__(
        self,
        name: str,
        unit: str,
        description: str,
        boundaries: Sequence[float],
    ):
        super().__init__(name, unit, description)
        self.boundaries = tuple(boundaries)

    def _create(self, meter):
        return meter.create_histogram(
            self.name,
            unit=self.unit,
            description=self.description,
            explicit_bucket_boundaries_advisory=list(self.boundaries),
        )

    def record(
        self,
        value: float,
        attributes: Optional[Mapping[str, Any]] = None,
        *,
        context=None,
    ) -> None:
        """record value, a no-op while telemetry is disabled"""
        self._record("record", value, attributes, context)


def _counter(name: str, *, unit: str = "", description: str = "") -> Counter:
    return Counter(name, unit, description)


def _histogram(
    name: str,
    *,
    unit: str = "s",
    description: str = "",
    boundaries: Sequence[float] = semconv.DURATION_BUCKETS,
) -> Histogram:
    return Histogram(name, unit, description, boundaries)


def _check_business(name: str) -> None:
    if not name.startswith(semconv.BUSINESS_PREFIX):
        raise ValueError(
            f"business metric names start with {semconv.BUSINESS_PREFIX!r}: {name!r}"
        )


def counter(name: str, *, unit: str = "", description: str = "") -> Counter:
    """a business counter, its name starts with maas.business."""
    _check_business(name)
    return _counter(name, unit=unit, description=description)


def histogram(
    name: str,
    *,
    unit: str = "s",
    description: str = "",
    boundaries: Sequence[float] = semconv.DURATION_BUCKETS,
) -> Histogram:
    """a business histogram, its name starts with maas.business."""
    _check_business(name)
    return _histogram(name, unit=unit, description=description, boundaries=boundaries)
