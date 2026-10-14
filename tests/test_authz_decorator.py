"""Tests for maas_model.authz's authorize decorator and permission helpers,
through a small FastAPI app with hand-written routes: what is checked, on
what, and what the caller gets back."""

import logging
from typing import Optional

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import APIRouter, FastAPI, Header, Response  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from opensearchpy import Keyword  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from maas_model.authz import (  # noqa: E402
    AuthorizerError,
    Plan,
    Principal,
    Requirement,
    Resource,
    RowFilter,
    authorize,
    install_authz,
    item_permissions,
    kind_permissions,
    permission_catalog,
    requirements,
)
from maas_model.document import MAASDocument  # noqa: E402

ALICE = Principal("alice", ("user",), {"missions": ["S1"]})

# R.attr.mission in P.attr.missions, as Cerbos would plan it for ALICE
MISSION_PLAN = Plan(
    kind=Plan.CONDITIONAL,
    condition={
        "expression": {
            "operator": "in",
            "operands": [
                {"variable": "request.resource.attr.mission"},
                {"value": ["S1"]},
            ],
        }
    },
)


class _Widget(MAASDocument):
    """A minimal pygen-shaped Document."""

    class Index:
        "inner class for DSL"

        name = "widget"

    name = Keyword()

    mission = Keyword()


class _WidgetIn(BaseModel):
    name: Optional[str] = None

    mission: Optional[str] = None


class _MissionAuthorizer:
    """Allows the documents of the principal's missions; records every call."""

    def __init__(self, plan=MISSION_PLAN, error=None):
        self.answer = plan
        self.error = error
        self.checks = []
        self.plans = []

    def check(self, principal, resources, actions):
        self.checks.append((list(resources), list(actions)))
        if self.error:
            raise self.error
        missions = principal.attributes.get("missions", [])
        return [
            {
                action: resource.attributes.get("mission") in missions
                for action in actions
            }
            for resource in resources
        ]

    def plan(self, principal, kind, action):
        self.plans.append((kind, action))
        if self.error:
            raise self.error
        return self.answer


@pytest.fixture(name="store")
def _store(monkeypatch):
    """two stored widgets, one per mission, served by _Widget.get_by_id"""
    store = {
        "1": _Widget(meta={"id": "1"}, name="left", mission="S1"),
        "2": _Widget(meta={"id": "2"}, name="right", mission="S2"),
    }
    monkeypatch.setattr(
        _Widget,
        "get_by_id",
        classmethod(lambda cls, document_id, **kwargs: store.get(document_id)),
    )
    return store


def _router(seen):
    router = APIRouter(prefix="/widget")

    @router.get("/")
    @authorize("widget:list", _Widget)
    def list_widgets(row_filter: RowFilter, size: int = 10):
        """List them."""
        seen["row_filter"] = None if row_filter is None else row_filter.to_dict()
        seen["size"] = size
        return []

    @router.get("/{item_id}")
    @authorize("widget:read", _Widget)
    def get_widget(item_id: str, document: _Widget):
        """Get one."""
        return {"id": item_id, "name": document.name}

    @router.post("/", status_code=201)
    @authorize("widget:create", _Widget)
    def create_widget(item: _WidgetIn):
        return item

    @router.put("/{item_id}")
    @authorize("widget:update", _Widget)
    def update_widget(item_id: str, item: _WidgetIn):
        return item

    @router.delete("/{item_id}", status_code=204)
    @authorize("widget:delete", _Widget)
    def delete_widget(item_id: str):
        seen["deleted"] = item_id
        return Response(status_code=204)

    return router


def _client(authorizer, principal=ALICE, install=True):
    """(client, router, seen): seen records what the handlers received"""
    seen = {}
    router = _router(seen)

    app = FastAPI()
    app.include_router(router)
    if install:
        install_authz(app, principal=lambda: principal, authorizer=authorizer)

    return TestClient(app), router, seen


# ---------------------------------------------------------------------------
# Item routes
# ---------------------------------------------------------------------------


def test_an_item_route_gets_the_checked_document(store):
    authorizer = _MissionAuthorizer()
    client, _, _ = _client(authorizer)

    response = client.get("/widget/1")

    assert response.status_code == 200
    assert response.json() == {"id": "1", "name": "left"}
    assert authorizer.checks == [
        (
            [Resource("widget", "1", {"name": "left", "mission": "S1"})],
            ["widget:read"],
        )
    ]


def test_an_item_the_policy_denies_is_403(store):
    client, _, seen = _client(_MissionAuthorizer())

    assert client.get("/widget/2").status_code == 403
    assert client.delete("/widget/2").status_code == 403
    assert "deleted" not in seen


def test_a_missing_item_is_404_without_asking(store):
    authorizer = _MissionAuthorizer()
    client, _, _ = _client(authorizer)

    response = client.get("/widget/404")

    assert response.status_code == 404
    assert response.json()["detail"] == "_Widget not found"
    assert authorizer.checks == []


def test_an_update_is_checked_on_the_stored_and_the_new_content(store):
    authorizer = _MissionAuthorizer()
    client, _, _ = _client(authorizer)

    # moving a document out of the caller's missions
    moved = client.put("/widget/1", json={"name": "left", "mission": "S2"})

    assert moved.status_code == 403
    [(resources, actions)] = authorizer.checks
    assert actions == ["widget:update"]
    assert [resource.attributes["mission"] for resource in resources] == ["S1", "S2"]
    assert {resource.id for resource in resources} == {"1"}

    assert client.put("/widget/1", json={"name": "lefter", "mission": "S1"}).json() == {
        "name": "lefter",
        "mission": "S1",
    }


# ---------------------------------------------------------------------------
# Create and collection routes
# ---------------------------------------------------------------------------


def test_a_creation_is_checked_on_the_body(store):
    authorizer = _MissionAuthorizer()
    client, _, _ = _client(authorizer)

    assert client.post("/widget/", json={"mission": "S1"}).status_code == 201
    assert client.post("/widget/", json={"mission": "S2"}).status_code == 403

    created, _ = authorizer.checks[0]
    assert created == [Resource("widget", "new", {"name": None, "mission": "S1"})]


def test_a_collection_route_gets_the_plan_as_a_filter(store):
    authorizer = _MissionAuthorizer()
    client, _, seen = _client(authorizer)

    response = client.get("/widget/?size=5")

    assert response.status_code == 200
    assert seen == {"row_filter": {"terms": {"mission": ["S1"]}}, "size": 5}
    assert authorizer.plans == [("widget", "widget:list")]


def test_a_collection_anyone_may_see_entirely_gets_no_filter(store):
    client, _, seen = _client(_MissionAuthorizer(plan=Plan(Plan.ALWAYS_ALLOWED)))

    assert client.get("/widget/").status_code == 200
    assert seen["row_filter"] is None


def test_a_collection_the_caller_may_see_nothing_of_is_403(store):
    client, _, seen = _client(_MissionAuthorizer(plan=Plan(Plan.ALWAYS_DENIED)))

    assert client.get("/widget/").status_code == 403
    assert seen == {}


def test_a_plan_with_no_translation_is_403_and_logged(store, caplog):
    untranslatable = Plan(
        kind=Plan.CONDITIONAL,
        condition={"expression": {"operator": "timeSince", "operands": []}},
    )
    client, _, seen = _client(_MissionAuthorizer(plan=untranslatable))

    with caplog.at_level(logging.ERROR):
        assert client.get("/widget/").status_code == 403

    assert seen == {}
    assert "no OpenSearch translation" in caplog.text


# ---------------------------------------------------------------------------
# Wiring and failures
# ---------------------------------------------------------------------------


def test_an_unreachable_pdp_is_503(store):
    client, _, _ = _client(_MissionAuthorizer(error=AuthorizerError("down")))

    assert client.get("/widget/1").status_code == 503
    assert client.get("/widget/").status_code == 503


def test_without_install_authz_protected_routes_answer_500(store):
    client, _, _ = _client(_MissionAuthorizer(), install=False)

    response = client.get("/widget/1")

    assert response.status_code == 500
    assert "install_authz" in response.json()["detail"]


def test_the_principal_dependency_may_read_the_request(store):
    """how a service plugs its identity in: here a trusted header"""

    def from_header(x_user: str = Header(...)) -> Principal:
        return Principal(x_user, ("user",), {"missions": ["S2"]})

    seen = {}
    app = FastAPI()
    app.include_router(_router(seen))
    install_authz(app, principal=from_header, authorizer=_MissionAuthorizer())
    client = TestClient(app)

    assert client.get("/widget/2", headers={"x-user": "bob"}).status_code == 200
    assert client.get("/widget/1", headers={"x-user": "bob"}).status_code == 403


def test_openapi_shows_neither_the_injected_parameters_nor_the_dependencies(store):
    client, _, _ = _client(_MissionAuthorizer())

    paths = client.app.openapi()["paths"]

    item = paths["/widget/{item_id}"]["get"]
    assert [parameter["name"] for parameter in item["parameters"]] == ["item_id"]
    assert item["description"] == (
        "Get one.\n\nRequires the Cerbos action `widget:read` on `widget`."
    )

    collection = paths["/widget/"]["get"]
    assert [parameter["name"] for parameter in collection["parameters"]] == ["size"]


# ---------------------------------------------------------------------------
# Requirements and permissions
# ---------------------------------------------------------------------------


def test_every_protected_route_carries_its_requirement():
    client, router, _ = _client(_MissionAuthorizer())

    # found through the routers an app includes, too
    assert permission_catalog(client.app.router) == permission_catalog(router)

    assert set(requirements(router)) == {
        Requirement("widget", "widget:list", Requirement.COLLECTION),
        Requirement("widget", "widget:read", Requirement.ITEM),
        Requirement("widget", "widget:create", Requirement.CREATE),
        Requirement("widget", "widget:update", Requirement.ITEM),
        Requirement("widget", "widget:delete", Requirement.ITEM),
    }
    assert permission_catalog(router) == {
        "widget": [
            "widget:create",
            "widget:delete",
            "widget:list",
            "widget:read",
            "widget:update",
        ]
    }


def test_kind_permissions_plan_the_collection_and_create_actions():
    _, router, _ = _client(_MissionAuthorizer())

    conditional = _MissionAuthorizer()
    assert kind_permissions(ALICE, conditional, router, "widget") == {
        "widget:create": True,
        "widget:list": True,
    }
    assert conditional.plans == [("widget", "widget:create"), ("widget", "widget:list")]

    denied = _MissionAuthorizer(plan=Plan(Plan.ALWAYS_DENIED))
    assert kind_permissions(ALICE, denied, router, "widget") == {
        "widget:create": False,
        "widget:list": False,
    }


def test_item_permissions_check_the_item_actions_at_once(store):
    _, router, _ = _client(_MissionAuthorizer())
    authorizer = _MissionAuthorizer()

    assert item_permissions(ALICE, authorizer, router, "widget", store["1"]) == {
        "widget:delete": True,
        "widget:read": True,
        "widget:update": True,
    }
    assert item_permissions(ALICE, authorizer, router, "widget", store["2"]) == {
        "widget:delete": False,
        "widget:read": False,
        "widget:update": False,
    }
    assert len(authorizer.checks) == 2


# ---------------------------------------------------------------------------
# Handlers the decorator refuses
# ---------------------------------------------------------------------------


def test_an_async_handler_is_refused():
    with pytest.raises(TypeError, match="async"):

        @authorize("widget:read", _Widget)
        async def get_widget(item_id: str):
            return item_id


def test_a_handler_that_does_not_say_what_to_check_is_refused():
    with pytest.raises(TypeError, match="needs either"):

        @authorize("widget:read", _Widget)
        def nothing(size: int = 10):
            return size


def test_a_document_without_an_item_id_is_refused():
    with pytest.raises(TypeError, match="without"):

        @authorize("widget:create", _Widget)
        def create_widget(item: _WidgetIn, document: _Widget):
            return item


def test_two_bodies_are_refused():
    with pytest.raises(TypeError, match="several bodies"):

        @authorize("widget:create", _Widget)
        def create_widget(item: _WidgetIn, other: _WidgetIn):
            return item
