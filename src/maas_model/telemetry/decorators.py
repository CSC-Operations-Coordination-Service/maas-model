"""
Decorators recording business metrics: timed, counted and record_latency.

Decorating raises ValueError for a bad definition (business name, ID-like
label, bad on). Calls never raise because of telemetry, and behave exactly
like the undecorated function while telemetry is disabled.
"""

import datetime
import functools
import inspect
import logging
from typing import Any, Callable, Mapping, Optional

import dateutil.parser

from maas_model.telemetry import metrics, semconv
from maas_model.telemetry._instruments import counter, histogram
from maas_model.telemetry._state import STATE

LOGGER = logging.getLogger("Telemetry")

_UTC = datetime.timezone.utc

OUTCOME_OK = "ok"
OUTCOME_ERROR = "error"


def _check_labels(labels) -> None:
    for label in labels:
        if semconv.ID_LABEL_DENYLIST.match(label):
            raise ValueError(f"metric label {label!r} looks like an identifier")


def _call_attributes(attributes, attributes_from, args, kwargs) -> dict:
    """static attributes plus attributes_from(*args, **kwargs), never raises"""
    result = dict(attributes) if attributes else {}
    if attributes_from is not None:
        try:
            result.update(attributes_from(*args, **kwargs))
        except Exception:  # pylint: disable=broad-except
            LOGGER.debug("attributes_from failed", exc_info=True)
    return result


def _is_generator(func) -> bool:
    return inspect.isgeneratorfunction(func) or inspect.isasyncgenfunction(func)


# timed


def timed(
    name: str,
    *,
    attributes: Optional[Mapping] = None,
    attributes_from: Optional[Callable[..., Mapping]] = None,
    boundaries=semconv.DURATION_BUCKETS,
):
    """
    record the duration in seconds of each call in the histogram name

    The attributes get outcome=ok or error. Works on sync, async and generator
    functions, a generator is timed from the first next() until exhaustion or
    close. attributes_from(*args, **kwargs) is evaluated at call time.
    """
    hist = histogram(name, boundaries=boundaries)
    if attributes:
        _check_labels(attributes)

    def decorate(func):
        import time

        clock = time.perf_counter

        def done(started, attrs, outcome):
            attrs["outcome"] = outcome
            hist.record(clock() - started, attrs)

        def begin(args, kwargs):
            return clock(), _call_attributes(attributes, attributes_from, args, kwargs)

        if inspect.isasyncgenfunction(func):

            @functools.wraps(func)
            async def agen_wrapper(*args, **kwargs):
                if not STATE.enabled:
                    async for item in func(*args, **kwargs):
                        yield item
                    return
                started, attrs = begin(args, kwargs)
                outcome = OUTCOME_OK
                inner = func(*args, **kwargs)
                try:
                    async for item in inner:
                        yield item
                except GeneratorExit:
                    raise
                except BaseException:
                    outcome = OUTCOME_ERROR
                    raise
                finally:
                    await inner.aclose()
                    done(started, attrs, outcome)

            return agen_wrapper

        if inspect.isgeneratorfunction(func):

            @functools.wraps(func)
            def gen_wrapper(*args, **kwargs):
                if not STATE.enabled:
                    return (yield from func(*args, **kwargs))
                started, attrs = begin(args, kwargs)
                outcome = OUTCOME_OK
                inner = func(*args, **kwargs)
                try:
                    return (yield from inner)
                except GeneratorExit:
                    raise
                except BaseException:
                    outcome = OUTCOME_ERROR
                    raise
                finally:
                    inner.close()
                    done(started, attrs, outcome)

            return gen_wrapper

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                if not STATE.enabled:
                    return await func(*args, **kwargs)
                started, attrs = begin(args, kwargs)
                outcome = OUTCOME_OK
                try:
                    return await func(*args, **kwargs)
                except BaseException:
                    outcome = OUTCOME_ERROR
                    raise
                finally:
                    done(started, attrs, outcome)

            return async_wrapper

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if not STATE.enabled:
                return func(*args, **kwargs)
            started, attrs = begin(args, kwargs)
            outcome = OUTCOME_OK
            try:
                return func(*args, **kwargs)
            except BaseException:
                outcome = OUTCOME_ERROR
                raise
            finally:
                done(started, attrs, outcome)

        return wrapper

    return decorate


# counted


def counted(
    name: str,
    *,
    amount: Optional[Callable[[Any], float]] = None,
    attributes: Optional[Mapping] = None,
    attributes_from: Optional[Callable[..., Mapping]] = None,
):
    """
    add to the counter name after each successful call

    Sync and async functions add amount(result), default 1, after they return.
    Generators add amount(item), default 1, per yielded item. Nothing is
    counted when the function raises.
    """
    ctr = counter(name)
    if attributes:
        _check_labels(attributes)

    def decorate(func):
        def add(value, attrs):
            try:
                ctr.add(1 if amount is None else amount(value), attrs)
            except Exception:  # pylint: disable=broad-except
                LOGGER.debug("counted %s: amount failed", name, exc_info=True)

        def attrs_of(args, kwargs):
            return _call_attributes(attributes, attributes_from, args, kwargs)

        if inspect.isasyncgenfunction(func):

            @functools.wraps(func)
            async def agen_wrapper(*args, **kwargs):
                attrs = attrs_of(args, kwargs) if STATE.enabled else None
                inner = func(*args, **kwargs)
                try:
                    async for item in inner:
                        if attrs is not None:
                            add(item, attrs)
                        yield item
                finally:
                    await inner.aclose()

            return agen_wrapper

        if inspect.isgeneratorfunction(func):

            @functools.wraps(func)
            def gen_wrapper(*args, **kwargs):
                if not STATE.enabled:
                    return (yield from func(*args, **kwargs))
                attrs = attrs_of(args, kwargs)
                inner = _Tap(func(*args, **kwargs), lambda item: add(item, attrs))
                try:
                    return (yield from inner)
                finally:
                    inner.close()

            return gen_wrapper

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                result = await func(*args, **kwargs)
                if STATE.enabled:
                    add(result, attrs_of(args, kwargs))
                return result

            return async_wrapper

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            if STATE.enabled:
                add(result, attrs_of(args, kwargs))
            return result

        return wrapper

    return decorate


class _Tap:
    """
    iterator over a generator calling hook(item) for each item it yields

    Implements send, throw and close so that yield from delegates to the
    generator transparently, and items are only pulled when requested.
    """

    def __init__(self, inner, hook):
        self._inner = inner
        self._hook = hook

    def __iter__(self):
        return self

    def _seen(self, item):
        try:
            self._hook(item)
        except Exception:  # pylint: disable=broad-except
            LOGGER.debug("telemetry hook failed", exc_info=True)
        return item

    def __next__(self):
        return self._seen(next(self._inner))

    def send(self, value):
        return self._seen(self._inner.send(value))

    def throw(self, *exc_info):
        return self._seen(self._inner.throw(*exc_info))

    def close(self):
        self._inner.close()


# record_latency


def _field_getter(path: str) -> Callable[[Any], Any]:
    segments = path.split(".")

    def get(item):
        for segment in segments:
            if item is None:
                return None
            if isinstance(item, Mapping):
                item = item.get(segment)
            else:
                item = getattr(item, segment, None)
        return item

    return get


def _accessor(spec) -> Callable[[Any], Any]:
    if callable(spec):
        return spec
    if not isinstance(spec, str) or not spec:
        raise ValueError(f"expected a field path, '@now' or a callable: {spec!r}")
    if spec == "@now":
        return lambda item: datetime.datetime.now(_UTC)
    return _field_getter(spec)


def _to_datetime(value) -> Optional[datetime.datetime]:
    """an aware datetime, or None. Naive values are UTC."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = dateutil.parser.isoparse(value)
        except (ValueError, OverflowError):
            LOGGER.debug("latency: cannot parse %r", value)
            return None
    if not isinstance(value, datetime.datetime):
        LOGGER.debug("latency: not a date: %r", value)
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=_UTC)
    return value


def record_latency(
    name: str,
    *,
    start,
    end,
    on: str = "return",
    attributes: Optional[Mapping[str, Any]] = None,
    boundaries=semconv.LATENCY_BUCKETS,
):
    """
    record (end - start) in seconds of items in the histogram name

    start and end are a dotted field path (attribute or dict key), "@now" or a
    callable item -> datetime | str | None. on is "return" (the result, each
    element of a list or tuple), "yield" (each item of a generator, lazily) or
    "arg:<param>" (the bound argument, each element of a list or tuple). A
    negative delta is counted in maas.latency.negative, and a missing or
    unparseable side is skipped.
    """
    if on == "return" or on == "yield":
        arg_name = None
    elif isinstance(on, str) and on.startswith("arg:") and len(on) > 4:
        arg_name = on[4:]
    else:
        raise ValueError(f"on must be 'return', 'yield' or 'arg:<param>': {on!r}")
    if attributes:
        _check_labels(attributes)
    hist = histogram(name, boundaries=boundaries)
    negative = metrics.LATENCY_NEGATIVE
    get_start = _accessor(start)
    get_end = _accessor(end)
    getters = {label: _accessor(spec) for label, spec in (attributes or {}).items()}

    def observe(item):
        try:
            began = _to_datetime(get_start(item))
            ended = _to_datetime(get_end(item))
            if began is None or ended is None:
                return
            delta = (ended - began).total_seconds()
            if delta < 0:
                negative.add(1, {"metric": name})
                return
            hist.record(delta, {label: get(item) for label, get in getters.items()})
        except Exception:  # pylint: disable=broad-except
            LOGGER.debug("latency %s: recording failed", name, exc_info=True)

    def observe_all(value):
        if isinstance(value, (list, tuple)):
            for item in value:
                observe(item)
        else:
            observe(value)

    def decorate(func):
        if on == "yield":
            if inspect.isasyncgenfunction(func):

                @functools.wraps(func)
                async def agen_wrapper(*args, **kwargs):
                    inner = func(*args, **kwargs)
                    try:
                        async for item in inner:
                            if STATE.enabled:
                                observe(item)
                            yield item
                    finally:
                        await inner.aclose()

                return agen_wrapper

            if not inspect.isgeneratorfunction(func):
                raise ValueError(
                    f"on='yield' needs a generator function: {func.__qualname__}"
                )

            @functools.wraps(func)
            def gen_wrapper(*args, **kwargs):
                if not STATE.enabled:
                    return (yield from func(*args, **kwargs))
                inner = _Tap(func(*args, **kwargs), observe)
                try:
                    return (yield from inner)
                finally:
                    inner.close()

            return gen_wrapper

        if arg_name is None and _is_generator(func):
            raise ValueError(
                f"on='return' records nothing for a generator function, "
                f"use on='yield': {func.__qualname__}"
            )

        if arg_name is not None:
            signature = inspect.signature(func)
            if arg_name not in signature.parameters:
                raise ValueError(f"{func.__qualname__} has no parameter {arg_name!r}")

            def bound_arg(args, kwargs):
                try:
                    bound = signature.bind(*args, **kwargs)
                    bound.apply_defaults()
                    return bound.arguments.get(arg_name)
                except TypeError:  # the call itself will raise
                    return None

            if inspect.iscoroutinefunction(func):

                @functools.wraps(func)
                async def async_arg_wrapper(*args, **kwargs):
                    if not STATE.enabled:
                        return await func(*args, **kwargs)
                    value = bound_arg(args, kwargs)
                    result = await func(*args, **kwargs)
                    observe_all(value)
                    return result

                return async_arg_wrapper

            @functools.wraps(func)
            def arg_wrapper(*args, **kwargs):
                if not STATE.enabled:
                    return func(*args, **kwargs)
                value = bound_arg(args, kwargs)
                result = func(*args, **kwargs)
                observe_all(value)
                return result

            return arg_wrapper

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                result = await func(*args, **kwargs)
                if STATE.enabled:
                    observe_all(result)
                return result

            return async_wrapper

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            if STATE.enabled:
                observe_all(result)
            return result

        return wrapper

    return decorate
