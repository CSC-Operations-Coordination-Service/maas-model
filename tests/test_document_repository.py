"""Tests for document_repository.py.

No live OpenSearch cluster is available in this environment, so these mock
the actual network boundary -- the raw client returned by
``Document._get_connection`` and the ``opensearchpy.helpers.bulk`` call --
while exercising real ``MAASDocument`` machinery (``to_bulk_action``,
``.meta``, field construction) end to end, so the orchestration logic in
this module is genuinely tested, not just its wiring to mocks.
"""

from typing import Any, Dict, List, Optional

from opensearchpy import Keyword

from maas_model import document_repository
from maas_model.document import MAASDocument
from maas_model.document_repository import (
	_IdCounter,
	create_document,
	delete_document,
	get_document,
	list_documents,
	next_id,
	update_document,
)


class _TestDocument(MAASDocument):
	"""A minimal pygen-shaped Document used to exercise the repository."""

	class Index:
		"inner class for DSL"

		name = "widget"

	name = Keyword()


class _FakeConnection:
	"""Records calls instead of hitting a real OpenSearch cluster."""

	def __init__(self, update_response: Optional[Dict[str, Any]] = None):
		self.update_calls: List[Dict[str, Any]] = []
		self.update_response = update_response or {}

	def update(self, **kwargs: Any) -> Dict[str, Any]:
		self.update_calls.append(kwargs)
		return self.update_response


class _FakeBulk:
	"""Stand-in for opensearchpy.helpers.bulk that just records actions."""

	def __init__(self) -> None:
		self.calls: List[Any] = []

	def __call__(self, connection: Any, actions: Any) -> Any:
		actions = list(actions)
		self.calls.append((connection, actions))
		return (len(actions), [])


def test_next_id_calls_scripted_update_and_parses_response(monkeypatch):
	fake_connection = _FakeConnection(
		update_response={"get": {"_source": {"value": 3}}}
	)
	monkeypatch.setattr(
		_IdCounter, "_get_connection", classmethod(lambda cls, using=None: fake_connection)
	)

	result = next_id("widget")

	assert result == 3
	assert len(fake_connection.update_calls) == 1
	call = fake_connection.update_calls[0]
	assert call["index"] == "id-counters"
	assert call["id"] == "widget"
	assert call["retry_on_conflict"] == 5
	assert call["_source"] is True
	assert call["body"]["upsert"] == {"value": 1}
	assert call["body"]["scripted_upsert"] is True
	assert "value += 1" in call["body"]["script"]["source"]


def test_list_documents_delegates_to_search(monkeypatch):
	documents = [_TestDocument(name="a"), _TestDocument(name="b")]

	class _FakeSearch:
		def execute(self):
			return documents

	monkeypatch.setattr(
		_TestDocument,
		"search",
		classmethod(lambda cls, using=None, index=None: _FakeSearch()),
	)

	assert list_documents(_TestDocument) == documents


def test_get_document_passes_id_through_and_ignores_missing_index(monkeypatch):
	calls = []

	def fake_get_by_id(cls, document_id, **kwargs):
		calls.append((document_id, kwargs))
		return _TestDocument(name="found")

	monkeypatch.setattr(_TestDocument, "get_by_id", classmethod(fake_get_by_id))

	result = get_document(_TestDocument, "5")

	assert result.name == "found"
	assert calls == [("5", {"ignore_missing_index": True})]


def test_get_document_returns_none_when_not_found(monkeypatch):
	monkeypatch.setattr(
		_TestDocument, "get_by_id", classmethod(lambda cls, document_id, **kwargs: None)
	)

	assert get_document(_TestDocument, "999") is None


def test_create_document_assigns_id_and_bulk_writes(monkeypatch):
	monkeypatch.setattr(document_repository, "next_id", lambda resource, using=None: 7)
	monkeypatch.setattr(
		_TestDocument, "_get_connection", classmethod(lambda cls, using=None: "conn")
	)
	fake_bulk = _FakeBulk()
	monkeypatch.setattr(document_repository, "bulk", fake_bulk)

	document = _TestDocument(name="widget-1")
	result = create_document(document)

	assert result is document
	assert document.meta.id == "7"
	assert len(fake_bulk.calls) == 1
	connection, actions = fake_bulk.calls[0]
	assert connection == "conn"
	assert len(actions) == 1
	assert actions[0]["_id"] == "7"
	assert actions[0]["_op_type"] == "create"


def test_update_document_returns_none_when_missing(monkeypatch):
	monkeypatch.setattr(
		_TestDocument, "get_by_id", classmethod(lambda cls, document_id, **kwargs: None)
	)
	fake_bulk = _FakeBulk()
	monkeypatch.setattr(document_repository, "bulk", fake_bulk)

	result = update_document(_TestDocument, "42", _TestDocument(name="new"))

	assert result is None
	assert fake_bulk.calls == []


def test_update_document_copies_concurrency_meta_and_bulk_writes(monkeypatch):
	existing = _TestDocument(
		meta={"id": "42", "index": "widget-2026", "seq_no": 3, "primary_term": 1},
		name="old",
	)
	monkeypatch.setattr(
		_TestDocument,
		"get_by_id",
		classmethod(lambda cls, document_id, **kwargs: existing),
	)
	monkeypatch.setattr(
		_TestDocument, "_get_connection", classmethod(lambda cls, using=None: "conn")
	)
	fake_bulk = _FakeBulk()
	monkeypatch.setattr(document_repository, "bulk", fake_bulk)

	new_document = _TestDocument(name="new")
	result = update_document(_TestDocument, "42", new_document)

	assert result is new_document
	assert new_document.meta.id == "42"
	assert new_document.meta.index == "widget-2026"
	assert new_document.meta.seq_no == 3
	assert new_document.meta.primary_term == 1
	assert len(fake_bulk.calls) == 1
	_, actions = fake_bulk.calls[0]
	assert actions[0]["_id"] == "42"
	# meta.index copied from the existing document -> the update targets the
	# same (already-partitioned) index it actually lives in, not Index.name
	assert actions[0]["_index"] == "widget-2026"
	# seq_no/primary_term present -> to_bulk_action resolves to "index", not "create"
	assert actions[0]["_op_type"] == "index"


def test_delete_document_returns_false_when_missing(monkeypatch):
	monkeypatch.setattr(
		_TestDocument, "get_by_id", classmethod(lambda cls, document_id, **kwargs: None)
	)
	fake_bulk = _FakeBulk()
	monkeypatch.setattr(document_repository, "bulk", fake_bulk)

	assert delete_document(_TestDocument, "1") is False
	assert fake_bulk.calls == []


def test_delete_document_bulk_deletes_when_found(monkeypatch):
	existing = _TestDocument(meta={"id": "1", "index": "widget"}, name="gone-soon")
	monkeypatch.setattr(
		_TestDocument,
		"get_by_id",
		classmethod(lambda cls, document_id, **kwargs: existing),
	)
	monkeypatch.setattr(
		_TestDocument, "_get_connection", classmethod(lambda cls, using=None: "conn")
	)
	fake_bulk = _FakeBulk()
	monkeypatch.setattr(document_repository, "bulk", fake_bulk)

	assert delete_document(_TestDocument, "1") is True
	assert len(fake_bulk.calls) == 1
	_, actions = fake_bulk.calls[0]
	assert actions[0] == {"_id": "1", "_index": "widget", "_op_type": "delete"}
