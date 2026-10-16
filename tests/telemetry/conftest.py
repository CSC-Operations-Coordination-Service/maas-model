import pytest

pytest.importorskip("opentelemetry.sdk")

from maas_model.telemetry import _state  # noqa: E402
from maas_model.telemetry.testing import telemetry_capture  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _reset_telemetry_state():
    """leave no telemetry state behind a test"""
    yield
    _state.reset()
