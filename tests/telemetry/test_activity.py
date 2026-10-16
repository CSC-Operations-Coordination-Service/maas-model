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
