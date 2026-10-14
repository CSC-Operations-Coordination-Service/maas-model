"""Tests for maas_model.authz.attributes: documents and request bodies as the
JSON attributes Cerbos conditions read."""

import datetime
import ipaddress
import json
from typing import List, Optional

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from opensearchpy import Ip, InnerDoc, Keyword, Object  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from maas_model.authz import document_attributes, schema_attributes  # noqa: E402
from maas_model.authz.attributes import json_safe  # noqa: E402
from maas_model.document import MAASDocument  # noqa: E402
from maas_model.zulu_date import ZuluDate  # noqa: E402


class _Origin(InnerDoc):
    station = Keyword()


class _Product(MAASDocument):
    """A pygen-shaped Document with a field of every shape attributes meet."""

    class Index:
        "inner class for DSL"

        name = "product"

    mission = Keyword()

    assignees = Keyword(multi=True)

    published = ZuluDate()

    source_ip = Ip()

    origin = Object(_Origin)


class _ProductIn(BaseModel):
    mission: Optional[str] = None

    assignees: Optional[List[str]] = None

    published: Optional[datetime.datetime] = None


PUBLISHED = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)


def test_every_mapped_field_is_present_null_when_unset():
    """so a policy guards with R.attr.x != null, which a check and a query
    plan read alike"""
    attributes = document_attributes(_Product(mission="S1"))

    # an empty array or object too: OpenSearch's exists, which a plan's
    # "!= null" becomes, sees none of them
    assert attributes == {
        "mission": "S1",
        "assignees": None,
        "published": None,
        "source_ip": None,
        "origin": None,
    }
    assert schema_attributes(_ProductIn(assignees=[]), _Product)["assignees"] is None


def test_document_values_are_json():
    product = _Product(
        mission="S1",
        assignees=["alice", "bob"],
        published=PUBLISHED,
        source_ip="10.0.0.1",
        origin=_Origin(station="MTI"),
    )

    attributes = document_attributes(product)

    assert attributes == {
        "mission": "S1",
        "assignees": ["alice", "bob"],
        "published": "2026-01-02T03:04:05Z",
        "source_ip": "10.0.0.1",
        "origin": {"station": "MTI"},
    }
    json.dumps(attributes)


def test_a_body_has_the_attributes_of_the_document_it_will_be():
    item = _ProductIn(mission="S2", assignees=["carol"], published=PUBLISHED)

    attributes = schema_attributes(item, _Product)

    # the fields the body lacks are null, like those of a stored document
    assert attributes == {
        "mission": "S2",
        "assignees": ["carol"],
        "published": "2026-01-02T03:04:05Z",
        "source_ip": None,
        "origin": None,
    }


@pytest.mark.parametrize(
    "value, expected",
    [
        (datetime.date(2026, 1, 2), "2026-01-02"),
        (
            datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc),
            "2026-01-02T03:04:05Z",
        ),
        (ipaddress.ip_address("::1"), "::1"),
        ((1, "a"), [1, "a"]),
        (
            {"nested": {"when": datetime.date(2026, 1, 2)}},
            {"nested": {"when": "2026-01-02"}},
        ),
        (b"raw", "b'raw'"),
    ],
)
def test_json_safe(value, expected):
    assert json_safe(value) == expected
