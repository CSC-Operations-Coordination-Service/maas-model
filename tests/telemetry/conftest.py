import pytest

pytest.importorskip("opentelemetry.sdk")

from maas_model.telemetry import _state  # noqa: E402
from maas_model.telemetry.activity import _reset_for_tests  # noqa: E402
from maas_model.telemetry.testing import telemetry_capture  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _reset_telemetry_state():
    """leave no telemetry state behind a test"""
    yield
    _state.reset()


@pytest.fixture(autouse=True)
def _reset_activity(request):
    """start without activity, and fail a test that leaves one behind"""
    _reset_for_tests()
    if "telemetry_capture" in request.fixturenames:
        # set the capture up from here, so that it is closed (which forgets
        # the activities) only after the check below
        request.getfixturevalue("telemetry_capture")
    yield
    leaked = _reset_for_tests()
    assert not leaked, f"activities left behind by the test: {leaked}"
