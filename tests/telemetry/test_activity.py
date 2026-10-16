from multiprocessing.pool import ThreadPool

from maas_model import telemetry


def test_activity_visible_in_threadpool(telemetry_capture):
    with telemetry.span("engine.run"), telemetry.activity("engine", "COMPUTE_X") as act:
        with ThreadPool(2) as pool:
            seen = pool.map(lambda _: telemetry.current_activity(), range(4))
    assert all(
        a.node == "COMPUTE_X" and a.span_context.span_id == act.span_context.span_id
        for a in seen
    )
    assert telemetry.current_activity() is None


def test_nested_activity_restores_outer(telemetry_capture):
    with telemetry.activity("engine", "OUTER"):
        with telemetry.activity("engine", "INNER"):
            assert telemetry.current_activity().node == "INNER"
        assert telemetry.current_activity().node == "OUTER"
    assert telemetry.current_activity() is None


def test_out_of_order_exit_keeps_stack_sane(telemetry_capture):
    import sys

    mod = sys.modules["maas_model.telemetry.activity"]

    a, b = telemetry.activity("t", "A"), telemetry.activity("t", "B")
    a.__enter__()
    b.__enter__()
    a.__exit__(None, None, None)
    assert [e.node for e in mod._stack] == ["B"]
    b.__exit__(None, None, None)
    assert mod._stack == []


def test_activity_disabled_returns_none():
    with telemetry.activity("engine", "X") as act:
        assert act is None
        assert telemetry.current_activity() is None


def test_out_of_order_exit_never_restores_exited_activity(telemetry_capture):
    import contextvars

    def out_of_order():
        a, b = telemetry.activity("t", "A"), telemetry.activity("t", "B")
        a.__enter__()
        b.__enter__()
        a.__exit__(None, None, None)
        assert telemetry.current_activity().node == "B"
        b.__exit__(None, None, None)
        assert telemetry.current_activity() is None

    def in_order():
        a, b = telemetry.activity("t", "A"), telemetry.activity("t", "B")
        a.__enter__()
        b.__enter__()
        b.__exit__(None, None, None)
        assert telemetry.current_activity().node == "A"
        a.__exit__(None, None, None)
        assert telemetry.current_activity() is None

    contextvars.copy_context().run(out_of_order)
    contextvars.copy_context().run(in_order)


def _activity_module():
    import sys

    return sys.modules["maas_model.telemetry.activity"]


def test_reset_for_tests_returns_and_clears_the_leaked_activities(
    telemetry_capture,
):
    mod = _activity_module()
    telemetry.activity("engine", "LEAKED").__enter__()
    assert [a.node for a in mod._reset_for_tests()] == ["LEAKED"]
    assert mod._stack == []
    assert mod._current.get() is None
    assert telemetry.current_activity() is None
    assert mod._reset_for_tests() == []


def test_capture_close_forgets_a_leaked_activity():
    from maas_model.telemetry.testing import capture

    mod = _activity_module()
    cap = capture()
    telemetry.activity("engine", "LEAKED").__enter__()
    cap.close()
    assert mod._stack == []
    assert mod._current.get() is None


def test_capture_starts_without_a_leaked_activity():
    from maas_model.telemetry.testing import capture

    first = capture()
    telemetry.activity("engine", "LEAKED").__enter__()
    second = capture()
    try:
        assert telemetry.current_activity() is None
    finally:
        second.close()
        first.close()


def test_activity_span_context_taken_when_entered(telemetry_capture):
    act = telemetry.activity("engine", "LATE")
    with telemetry.span("run"):
        expected = telemetry.current_span_context()
        with act as entered:
            assert entered.span_context == expected
            assert telemetry.current_activity().span_context == expected
