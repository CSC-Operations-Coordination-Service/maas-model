"""
Overhead budget: under 1 microsecond per call while telemetry is disabled.
"""

import sys
import timeit

import pytest

from maas_model import telemetry
from maas_model.telemetry import _state

CALLS = 100_000

REPEAT = 5

BUDGET_S = 1e-6


def per_call(func) -> float:
    return min(timeit.repeat(func, number=CALLS, repeat=REPEAT)) / CALLS


@pytest.mark.skipif(
    sys.gettrace() is not None,
    reason="a trace function (coverage) slows every call 5 to 10 times: "
    "run with --no-cov to check the budget",
)
def test_disabled_overhead_under_1us():
    _state.reset()
    ctr = telemetry.counter("maas.business.o")
    hist = telemetry.histogram("maas.business.oh")

    def enter_exit():
        with telemetry.span("x"):
            pass

    def baggage_enter_exit():
        with telemetry.baggage(a="b"):
            pass

    measured = {
        "baggage enter/exit": per_call(baggage_enter_exit),
        "counter.add": per_call(lambda: ctr.add(1, {"a": "b"})),
        "histogram.record": per_call(lambda: hist.record(1.0, {"a": "b"})),
        "span enter/exit": per_call(enter_exit),
    }
    print({name: f"{value * 1e9:.0f} ns" for name, value in measured.items()})
    for name, value in measured.items():
        assert value < BUDGET_S, f"{name}: {value * 1e9:.0f} ns"
