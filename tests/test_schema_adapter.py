from datetime import datetime, timezone
from typing import Optional

import pytest

pytest.importorskip("pydantic")

from opensearchpy import Keyword  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from maas_model.document import MAASDocument  # noqa: E402
from maas_model.schema_adapter import (  # noqa: E402
    document_to_schema,
    schema_to_document,
)
from maas_model.zulu_date import ZuluDate  # noqa: E402


class _TestDocument(MAASDocument):
    """Minimal pygen-shaped Document used to exercise the adapter."""

    class Index:
        "inner class for DSL"

        name = "test"

    id = Keyword()
    name = Keyword()
    created_at = ZuluDate()
    internal_note = Keyword()


class _TestSchema(BaseModel):
    """Minimal fastapigen-shaped schema used to exercise the adapter."""

    id: str
    name: str
    created_at: datetime
    extra_note: Optional[str] = None


def test_document_to_schema_copies_only_shared_fields():
    document = _TestDocument(
        meta={"id": "abc"},
        id="1",
        name="example",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        internal_note="secret",
    )

    schema = document_to_schema(document, _TestSchema)

    assert schema.id == "1"
    assert schema.name == "example"
    assert schema.created_at == datetime(2026, 1, 1, tzinfo=timezone.utc)

    # Document-only field never reaches the schema.
    # Schema-only field keeps its own default.
    assert schema.extra_note is None


def test_schema_to_document_copies_only_shared_fields_and_sets_id():
    schema = _TestSchema(
        id="1",
        name="example",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        extra_note="ignored",
    )

    document = schema_to_document(
        schema,
        _TestDocument,
        document_id="doc-1",
    )

    assert document.meta.id == "doc-1"
    assert document.id == "1"
    assert document.name == "example"
    assert document.created_at == datetime(2026, 1, 1, tzinfo=timezone.utc)

    # Schema-only field never reaches the document.
    # Document-only field is left unset.
    assert document.internal_note is None


def test_schema_to_document_without_id_leaves_meta_id_unset():
    schema = _TestSchema(
        id="1",
        name="example",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    document = schema_to_document(schema, _TestDocument)

    assert "id" not in document.meta