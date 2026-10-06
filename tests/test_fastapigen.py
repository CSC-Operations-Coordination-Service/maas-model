"""Tests for fastapigen.py: schema generation, router generation, the
aggregate router index, error handling, and one end-to-end CRUD flow
through the generated FastAPI app.
"""

import importlib
import json
import sys

import pytest

from maas_model import document_repository
from maas_model.generator.fastapigen import (
	RouterGenerator,
	SchemaGenerator,
	generate_router_index,
	generate_routers,
	generate_schemas,
)
from maas_model.generator.meta import FieldMeta, ModelClassMeta
from maas_model.generator.pygen import generate as generate_pygen


# ---------------------------------------------------------------------------
# Template fixtures
# ---------------------------------------------------------------------------


def _write_widget_template(path):
	"""Minimal, keyword-only template used by most tests below.

	Deliberately small and date-free: these tests are about router/schema
	wiring, not field-type coverage (see
	test_schema_generator_maps_every_supported_type for that), and staying
	date-free keeps them decoupled from ZuluDate's own serialization
	behavior.
	"""
	template = {
		"mappings": {
			"properties": {
				"id": {"type": "keyword"},
				"name": {"type": "keyword"},
			}
		}
	}
	path.write_text(json.dumps(template), encoding="UTF-8")


def _write_widget_template_with_every_type(path):
	"""A field of every ES type fastapigen maps, for schema-generation
	coverage."""
	template = {
		"mappings": {
			"properties": {
				"id": {"type": "text"},
				"name": {"type": "keyword"},
				"priority": {"type": "integer"},
				"retry_count": {"type": "long"},
				"score": {"type": "float"},
				"error_rate": {"type": "double"},
				"enabled": {"type": "boolean"},
				"created_at": {"type": "date"},
				"source_ip": {"type": "ip"},
			}
		}
	}
	path.write_text(json.dumps(template), encoding="UTF-8")


def _write_nested_widget_template(path):
	# Nested `object` fields aren't supported by SchemaGenerator yet --
	# used to test both the schema-generator error path and router gating.
	template = {
		"mappings": {
			"properties": {
				"id": {"type": "keyword"},
				"metadata": {
					"type": "object",
					"properties": {"source": {"type": "keyword"}},
				},
			}
		}
	}
	path.write_text(json.dumps(template), encoding="UTF-8")


# ---------------------------------------------------------------------------
# SchemaGenerator
# ---------------------------------------------------------------------------


def test_schema_generator_maps_every_supported_type(tmp_path):
	tpl = tmp_path / "widget_template.json"
	_write_widget_template_with_every_type(tpl)

	meta = ModelClassMeta(str(tpl))
	meta.load()

	source = SchemaGenerator(meta).generate()

	assert meta.class_name == "Widget"
	assert "from pydantic import BaseModel" in source
	assert "from datetime import datetime" in source
	assert "from pydantic import IPvAnyAddress" in source
	assert "class Widget(BaseModel):" in source

	# text/keyword -> str
	assert "    id: str" in source
	assert "    name: str" in source
	assert "    source_ip: IPvAnyAddress" in source

	# integer/long -> int
	assert "    priority: int" in source
	assert "    retry_count: int" in source

	# float/double -> float
	assert "    score: float" in source
	assert "    error_rate: float" in source

	# boolean -> bool
	assert "    enabled: bool" in source

	# date -> datetime
	assert "    created_at: datetime" in source


def test_schema_generator_unsupported_type_raises_value_error():
	meta = ModelClassMeta("widget_template.json")
	meta.fields = [FieldMeta(name="footprint", type_name="GeoShape", properties=[])]

	with pytest.raises(ValueError, match="Unsupported field type"):
		SchemaGenerator(meta).generate()


def test_schema_generator_nested_object_raises_value_error(tmp_path):
	tpl = tmp_path / "widget_template.json"
	_write_nested_widget_template(tpl)

	meta = ModelClassMeta(str(tpl))
	meta.load()

	with pytest.raises(ValueError, match="Nested object fields are not supported"):
		SchemaGenerator(meta).generate()


# ---------------------------------------------------------------------------
# RouterGenerator / generate_routers / generate_router_index
# ---------------------------------------------------------------------------


def test_router_generator_source_shape(tmp_path):
	tpl = tmp_path / "widget_template.json"
	_write_widget_template(tpl)

	meta = ModelClassMeta(str(tpl))
	meta.load()

	source = RouterGenerator(meta).generate()

	assert "from schemas.widget import Widget" in source
	assert "from documents.widget import Widget as WidgetDocument" in source
	assert 'router = APIRouter(prefix="/widget", tags=["widget"])' in source
	assert "from maas_model.document_repository import" in source
	assert (
		"from maas_model.schema_adapter import document_to_schema, schema_to_document"
		in source
	)
	assert "_STORE" not in source  # no more in-memory placeholder

	assert "def list_widget() -> List[Widget]:" in source
	assert "list_documents(WidgetDocument)" in source

	assert "def get_widget(item_id: str) -> Widget:" in source
	assert "get_document(WidgetDocument, item_id)" in source

	assert "def create_widget(item: Widget, response: Response) -> Widget:" in source
	assert "create_document(schema_to_document(item, WidgetDocument))" in source

	assert "def update_widget(item_id: str, item: Widget) -> Widget:" in source
	assert "update_document(" in source

	assert "def delete_widget(item_id: str) -> Response:" in source
	assert "delete_document(WidgetDocument, item_id)" in source


def test_generate_routers_skips_templates_whose_schema_failed(tmp_path):
	templates_dir = tmp_path / "templates"
	templates_dir.mkdir()
	_write_widget_template(templates_dir / "widget_template.json")
	_write_nested_widget_template(templates_dir / "broken_template.json")

	output_dir = tmp_path / "generated"

	schema_paths, schema_errors = generate_schemas(templates_dir, output_dir)
	router_paths, router_errors = generate_routers(
		templates_dir,
		output_dir,
		available_modules={path.stem for path in schema_paths},
	)

	# widget's schema succeeds, broken's fails (nested object)
	assert [path.stem for path in schema_paths] == ["widget"]
	assert [path.name for path, _ in schema_errors] == ["broken_template.json"]

	# router is only generated for the template whose schema succeeded
	assert [path.stem for path in router_paths] == ["widget"]
	assert [path.name for path, _ in router_errors] == ["broken_template.json"]


def test_generate_routers_without_gating_generates_every_template(tmp_path):
	"""``available_modules=None`` (the default) skips the gate entirely."""
	templates_dir = tmp_path / "templates"
	templates_dir.mkdir()
	_write_widget_template(templates_dir / "widget_template.json")

	output_dir = tmp_path / "generated"
	router_paths, router_errors = generate_routers(templates_dir, output_dir)

	assert [path.stem for path in router_paths] == ["widget"]
	assert not router_errors


def test_generate_router_index_aggregates_every_resource(tmp_path):
	output_dir = tmp_path / "generated"

	index_path = generate_router_index(["widget", "gadget"], output_dir)

	assert index_path == output_dir / "routers" / "__init__.py"
	source = index_path.read_text(encoding="UTF-8")

	assert "from routers.widget import router as widget_router" in source
	assert "from routers.gadget import router as gadget_router" in source
	assert "router = APIRouter()" in source
	assert "router.include_router(widget_router)" in source
	assert "router.include_router(gadget_router)" in source


def test_generate_router_index_with_no_resources_is_still_valid_python(tmp_path):
	output_dir = tmp_path / "generated"

	index_path = generate_router_index([], output_dir)

	compile(index_path.read_text(encoding="UTF-8"), str(index_path), "exec")


# ---------------------------------------------------------------------------
# End-to-end: generated schema + router + pygen document, served over HTTP
# ---------------------------------------------------------------------------


def _install_fake_persistence(document_cls, monkeypatch):
	"""Fake only the OpenSearch network boundary for ``document_cls``.

	Patches ``_get_connection``/``get_by_id``/``search`` (classmethods) and
	``document_repository.bulk`` with an in-memory store, so the real
	router -> document_repository -> schema_adapter -> MAASDocument chain
	can be driven through actual HTTP calls without a live cluster --
	``to_bulk_action()``, ``.meta`` handling, and field construction all
	still run for real.
	"""
	store = {}
	counters = {}

	class _FakeCounterConnection:
		def update(self, index, id, body, **kwargs):  # noqa: A002
			counters[id] = counters.get(id, 0) + 1
			return {"get": {"_source": {"value": counters[id]}}}

	def fake_bulk(connection, actions):
		for action in actions:
			if action["_op_type"] == "delete":
				store.pop(action["_id"], None)
				continue
			# a real search hit always carries `_index` in its meta (see
			# MAASDocument.mget_by_ids); to_bulk_action()'s delete path
			# reads meta.index, so the fake needs to set it too
			instance = document_cls(
				meta={"id": action["_id"], "index": action["_index"]},
				**action["_source"],
			)
			instance.meta.seq_no = 1
			instance.meta.primary_term = 1
			store[action["_id"]] = instance
		return (len(actions), [])

	class _FakeSearch:
		def execute(self):
			return list(store.values())

	monkeypatch.setattr(
		document_repository._IdCounter,
		"_get_connection",
		classmethod(lambda cls, using=None: _FakeCounterConnection()),
	)
	monkeypatch.setattr(
		document_cls, "_get_connection", classmethod(lambda cls, using=None: "fake-conn")
	)
	monkeypatch.setattr(
		document_cls,
		"get_by_id",
		classmethod(lambda cls, document_id, **kwargs: store.get(document_id)),
	)
	monkeypatch.setattr(
		document_cls,
		"search",
		classmethod(lambda cls, using=None, index=None: _FakeSearch()),
	)
	monkeypatch.setattr(document_repository, "bulk", fake_bulk)
	return store


@pytest.mark.parametrize("index_name", ["widget", "dm-widget"])
def test_generated_schema_and_router_serve_crud(index_name, tmp_path, monkeypatch):
	"""End-to-end: generate real schema/router/pygen-document files, import
	them, and drive the full CRUD flow through real HTTP calls -- only the
	OpenSearch network boundary is faked (see ``_install_fake_persistence``).

	``dm-widget`` covers kebab-case index names (e.g. raw-data-dm-cdse-product):
	modules/identifiers use underscores, the URL keeps the hyphens.
	"""
	fastapi = pytest.importorskip("fastapi")
	testclient = pytest.importorskip("fastapi.testclient")

	templates_dir = tmp_path / "templates"
	templates_dir.mkdir()
	template_path = templates_dir / f"{index_name}_template.json"
	_write_widget_template(template_path)
	meta = ModelClassMeta(str(template_path))
	module, class_name = meta.module_name, meta.class_name

	output_dir = tmp_path / "generated"
	schema_paths, schema_errors = generate_schemas(templates_dir, output_dir)
	router_paths, router_errors = generate_routers(
		templates_dir,
		output_dir,
		available_modules={path.stem for path in schema_paths},
	)
	assert not schema_errors
	assert not router_errors
	generate_router_index([path.stem for path in router_paths], output_dir)

	# The router also expects a sibling pygen-generated Document module at
	# documents.<module> (fastapigen doesn't generate this -- see
	# RouterGenerator's docstring). Generate it for real via pygen, at the
	# documented naming convention, to prove that convention actually works.
	documents_dir = output_dir / "documents"
	documents_dir.mkdir(parents=True, exist_ok=True)
	document_source = generate_pygen(str(template_path))
	(documents_dir / f"{module}.py").write_text(document_source, encoding="UTF-8")

	for module_name in (
		"schemas",
		f"schemas.{module}",
		"routers",
		f"routers.{module}",
		"documents",
		f"documents.{module}",
	):
		monkeypatch.delitem(sys.modules, module_name, raising=False)

	monkeypatch.syspath_prepend(str(output_dir))
	importlib.invalidate_caches()

	document_cls = getattr(importlib.import_module(f"documents.{module}"), class_name)
	store = _install_fake_persistence(document_cls, monkeypatch)

	# the aggregate index is what an app actually mounts
	routers_package = importlib.import_module("routers")

	app = fastapi.FastAPI()
	app.include_router(routers_package.router)
	client = testclient.TestClient(app)
	url = f"/{index_name}"

	assert client.get(f"{url}/").json() == []

	payload = {"id": "w1", "name": "left-flange"}
	create_response = client.post(f"{url}/", json=payload)
	assert create_response.status_code == 201
	assert create_response.headers["location"] == f"{url}/1"  # fake counter
	assert create_response.json() == payload

	get_response = client.get(f"{url}/1")
	assert get_response.status_code == 200
	assert get_response.json()["name"] == "left-flange"

	assert len(client.get(f"{url}/").json()) == 1

	updated_payload = {"id": "w1", "name": "right-flange"}
	put_response = client.put(f"{url}/1", json=updated_payload)
	assert put_response.status_code == 200
	assert put_response.json()["name"] == "right-flange"

	delete_response = client.delete(f"{url}/1")
	assert delete_response.status_code == 204

	assert client.get(f"{url}/1").status_code == 404
	assert client.put(f"{url}/1", json=updated_payload).status_code == 404
	assert client.delete(f"{url}/1").status_code == 404

	# documents written outside the API (collector/engine) carry string _ids
	# that never came from the id-counters index -- they must be reachable too
	external_id = "S1A_IW_GRDH_1SDV_20260101T000000"
	store[external_id] = document_cls(
		meta={"id": external_id, "index": index_name}, id="p1", name="product"
	)
	external_response = client.get(f"{url}/{external_id}")
	assert external_response.status_code == 200
	assert external_response.json()["name"] == "product"
	assert client.delete(f"{url}/{external_id}").status_code == 204
	assert external_id not in store
