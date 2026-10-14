"""Tests for maas_model.authz.authorizer: what CerbosAuthorizer sends to the
PDP and how it reads the answers (an httpx.MockTransport stands in for the
PDP, answering with responses captured from a real one), and
AllowAllAuthorizer."""

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from maas_model.authz import (  # noqa: E402
    AllowAllAuthorizer,
    AuthorizerError,
    CerbosAuthorizer,
    Plan,
    Principal,
    Resource,
)

CERBOS_DATA = Path(__file__).parent / "data" / "cerbos"

ALICE = Principal("alice", ("user",), {"grants": [{"mission": "S1"}]})


def _authorizer(handler):
    """a CerbosAuthorizer whose PDP is ``handler(request) -> httpx.Response``,
    recording the JSON bodies it receives"""
    requests = []

    def record(request):
        requests.append((request.url.path, json.loads(request.content)))
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(record))
    return CerbosAuthorizer("http://cerbos:3592/", client=client), requests


def _echo_check(allowed_ids):
    """a CheckResources PDP allowing every action on the given resource ids"""

    def handler(request):
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "resource": {"id": entry["resource"]["id"]},
                        "actions": {
                            action: (
                                "EFFECT_ALLOW"
                                if entry["resource"]["id"] in allowed_ids
                                else "EFFECT_DENY"
                            )
                            for action in entry["actions"]
                        },
                    }
                    for entry in body["resources"]
                ]
            },
        )

    return handler


def test_check_sends_the_principal_and_the_resources():
    captured = json.loads((CERBOS_DATA / "check_response.json").read_text())
    authorizer, requests = _authorizer(
        lambda request: httpx.Response(200, json=captured)
    )

    decisions = authorizer.check(
        ALICE,
        [
            Resource("widget", "1", {"mission": "S1", "phase": None}),
            Resource("widget", "2", {"mission": None, "phase": None}),
            Resource("widget", "3", {"mission": "S2", "phase": None}),
        ],
        ["widget:read", "widget:update"],
    )

    # the captured answer only decided widget:read for 2 and 3: an action
    # missing from the answer is denied
    assert decisions == [
        {"widget:read": True, "widget:update": True},
        {"widget:read": False, "widget:update": False},
        {"widget:read": False, "widget:update": False},
    ]

    [(path, body)] = requests
    assert path == "/api/check/resources"
    assert body["principal"] == {
        "id": "alice",
        "roles": ["user"],
        "attr": {"grants": [{"mission": "S1"}]},
    }
    assert body["resources"][0] == {
        "actions": ["widget:read", "widget:update"],
        "resource": {
            "kind": "widget",
            "id": "1",
            "attr": {"mission": "S1", "phase": None},
        },
    }


def test_check_splits_more_than_50_resources_across_requests():
    authorizer, requests = _authorizer(_echo_check({"0", "50", "119"}))
    resources = [Resource("widget", str(index)) for index in range(120)]

    decisions = authorizer.check(ALICE, resources, ["widget:read"])

    assert [len(body["resources"]) for _, body in requests] == [50, 50, 20]
    assert [
        index for index, decision in enumerate(decisions) if decision["widget:read"]
    ] == [0, 50, 119]


def test_a_principal_without_roles_is_anonymous():
    authorizer, requests = _authorizer(_echo_check(set()))

    authorizer.check(Principal("nobody"), [Resource("widget", "1")], ["widget:read"])

    assert requests[0][1]["principal"]["roles"] == ["anonymous"]


@pytest.mark.parametrize(
    "name, kind",
    [
        ("super", Plan.ALWAYS_ALLOWED),
        ("denied", Plan.ALWAYS_DENIED),
        ("grants_user", Plan.CONDITIONAL),
    ],
)
def test_plan_reads_the_filter(name, kind):
    captured = json.loads((CERBOS_DATA / "plans" / f"{name}.json").read_text())
    authorizer, requests = _authorizer(
        lambda request: httpx.Response(200, json=captured)
    )

    plan = authorizer.plan(ALICE, "widget", "widget:list")

    assert plan.kind == kind
    assert plan.condition == captured["filter"].get("condition")

    [(path, body)] = requests
    assert path == "/api/plan/resources"
    assert body["action"] == "widget:list"
    assert body["resource"] == {"kind": "widget"}
    assert body["principal"]["id"] == "alice"


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(500, json={"message": "boom"}),
        lambda request: httpx.Response(200, content=b"not json"),
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused")),
    ],
)
def test_a_pdp_that_cannot_answer_raises_authorizer_error(handler):
    authorizer, _ = _authorizer(handler)

    with pytest.raises(AuthorizerError):
        authorizer.plan(ALICE, "widget", "widget:list")


def test_a_check_answer_missing_results_raises_authorizer_error():
    authorizer, _ = _authorizer(
        lambda request: httpx.Response(200, json={"results": []})
    )

    with pytest.raises(AuthorizerError):
        authorizer.check(ALICE, [Resource("widget", "1")], ["widget:read"])


def test_allow_all_allows_everything():
    authorizer = AllowAllAuthorizer()

    assert authorizer.check(
        ALICE, [Resource("widget", "1"), Resource("widget", "2")], ["a", "b"]
    ) == [{"a": True, "b": True}, {"a": True, "b": True}]
    assert authorizer.plan(ALICE, "widget", "widget:list").kind == Plan.ALWAYS_ALLOWED
