"""
The span handed out while telemetry is disabled.
"""


class NoopSpan:
    """a span that records nothing, and costs next to nothing"""

    def set_attribute(self, key, value):
        pass

    def set_attributes(self, attributes):
        pass

    def add_event(self, name, attributes=None, timestamp=None):
        pass

    def record_exception(
        self, exception, attributes=None, timestamp=None, escaped=False
    ):
        pass

    def set_status(self, status, description=None):
        pass

    def is_recording(self) -> bool:
        return False

    def get_span_context(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


NOOP_SPAN = NoopSpan()
