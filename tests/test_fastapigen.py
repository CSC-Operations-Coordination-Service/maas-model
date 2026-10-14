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
	generate_policies,
	generate_router_index,
	generate_routers,
	generate_schemas,
	python_module_name,
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
	assert "    id: Optional[str] = None" in source
	assert "    name: Optional[str] = None" in source
	assert "    source_ip: Optional[IPvAnyAddress] = None" in source

	# integer/long -> int
	assert "    priority: Optional[int] = None" in source
	assert "    retry_count: Optional[int] = None" in source

	# float/double -> float
	assert "    score: Optional[float] = None" in source
	assert "    error_rate: Optional[float] = None" in source

	# boolean -> bool
	assert "    enabled: Optional[bool] = None" in source

	# date -> datetime
	assert "    created_at: Optional[datetime] = None" in source


def test_schema_fields_are_optional_and_carry_the_document_id(tmp_path):
	"""documents written by someone else may miss any field"""
	tpl = tmp_path / "widget_template.json"
	_write_template(tpl, {"name": {"type": "keyword"}})

	meta = ModelClassMeta(str(tpl))
	meta.load()

	source = SchemaGenerator(meta).generate()

	assert "from typing import Optional" in source
	assert "    document_id: Optional[str] = None" in source
	assert "    name: Optional[str] = None" in source
	compile(source, "widget.py", "exec")


def test_a_free_form_schema_keeps_the_fields_its_template_does_not_map(tmp_path):
	"""a free-form index (mappings._meta.free_form) holds documents the mapping
	cannot list every field of, e.g. maas-collector configurations"""
	tpl = tmp_path / "widget_template.json"
	_write_template(tpl, {"name": {"type": "keyword"}}, meta={"free_form": True})

	meta = ModelClassMeta(str(tpl))
	meta.load()
	namespace = {}
	exec(compile(SchemaGenerator(meta).generate(), "widget.py", "exec"), namespace)

	widget = namespace["Widget"].model_validate(
		{"name": "w", "class": "C", "attr_map": {"a": 1}}
	)

	assert widget.model_dump() == {
		"document_id": None,
		"name": "w",
		"class": "C",
		"attr_map": {"a": 1},
	}


def test_a_schema_drops_the_fields_its_template_does_not_map(tmp_path):
	tpl = tmp_path / "widget_template.json"
	_write_template(tpl, {"name": {"type": "keyword"}})

	meta = ModelClassMeta(str(tpl))
	meta.load()
	namespace = {}
	exec(compile(SchemaGenerator(meta).generate(), "widget.py", "exec"), namespace)

	widget = namespace["Widget"].model_validate({"name": "w", "class": "C"})

	assert widget.model_dump() == {"document_id": None, "name": "w"}


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
	assert 'KIND = "widget"' in source

	# every route is checked by Cerbos, ids are the documents' _id
	assert '@authorize("widget:list", WidgetDocument, kind=KIND)' in source
	assert "def list_widget(\n    row_filter: RowFilter," in source
	assert "offset: int = Query(0, ge=0, le=9000)" in source
	assert "offset=offset, size=size, query=row_filter" in source

	assert '@authorize("widget:read", WidgetDocument, kind=KIND)' in source
	assert (
		"def get_widget(item_id: str, document: WidgetDocument) -> Widget:" in source
	)

	assert '@authorize("widget:create", WidgetDocument, kind=KIND)' in source
	assert "def create_widget(item: Widget, response: Response) -> Widget:" in source
	assert "create_document(schema_to_document(item, WidgetDocument))" in source

	assert '@authorize("widget:update", WidgetDocument, kind=KIND)' in source
	assert "def update_widget(item_id: str, item: Widget) -> Widget:" in source
	assert "update_document(" in source

	assert '@authorize("widget:delete", WidgetDocument, kind=KIND)' in source
	assert "def delete_widget(item_id: str) -> Response:" in source
	assert "delete_document(WidgetDocument, item_id)" in source

	# the caller's permissions, on the index and on one document; the index
	# route is declared before /{item_id}, which would take it for an id
	assert source.index('"/_permissions"') < source.index('"/{item_id}"')
	assert "kind_permissions(principal, authorizer, router, KIND)" in source
	assert '@router.get("/{item_id}/permissions"' in source
	assert "item_permissions(principal, authorizer, router, KIND, document)" in source


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
	assert '@router.get("/permissions"' in source
	assert "return permission_catalog(router)" in source


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

	Returns:
		the list the filters applied to searches are recorded in, as dicts.
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

	filters = []

	class _FakeSearch:
		"""returns every stored document, recording the filters applied"""

		def filter(self, query):
			filters.append(query.to_dict())
			return self

		def __getitem__(self, key):
			return self

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

	return filters


class _MissionAuthorizer:
	"""Allows the documents of the principal's missions, as a Cerbos policy on
	R.attr.mission would."""

	def check(self, principal, resources, actions):
		missions = principal.attributes.get("missions", [])
		return [
			{action: resource.attributes.get("mission") in missions for action in actions}
			for resource in resources
		]

	def plan(self, principal, kind, action):
		from maas_model.authz import Plan

		return Plan(
			kind=Plan.CONDITIONAL,
			condition={
				"expression": {
					"operator": "in",
					"operands": [
						{"variable": "request.resource.attr.mission"},
						{"value": principal.attributes.get("missions", [])},
					],
				}
			},
		)


def _serve_generated_widget(tmp_path, monkeypatch, authorizer, principal, meta=None):
	"""Generate the widget schema, router, router index and pygen document,
	import them, and serve them with ``authorizer`` deciding for
	``principal`` -- only the OpenSearch network boundary is faked (see
	``_install_fake_persistence``).

	Returns:
		(client, the Widget document class, the recorded search filters)
	"""
	fastapi = pytest.importorskip("fastapi")
	testclient = pytest.importorskip("fastapi.testclient")
	from maas_model.authz import install_authz

	templates_dir = tmp_path / "templates"
	templates_dir.mkdir()
	template_path = templates_dir / "widget_template.json"
	_write_template(
		template_path,
		{
			"id": {"type": "keyword"},
			"name": {"type": "keyword"},
			"mission": {"type": "keyword"},
		},
		meta=meta,
	)

	output_dir = tmp_path / "generated"
	schema_paths, schema_errors = generate_schemas(templates_dir, output_dir)
	router_paths, router_errors = generate_routers(
		templates_dir,
		output_dir,
		available_modules={path.stem for path in schema_paths},
	)
	generate_router_index([path.stem for path in router_paths], output_dir)
	assert not schema_errors
	assert not router_errors

	# The router also expects a sibling pygen-generated Document module at
	# documents.<model> (fastapigen doesn't generate this -- see
	# RouterGenerator's docstring). Generate it for real via pygen, at the
	# documented naming convention, to prove that convention actually works.
	documents_dir = output_dir / "documents"
	documents_dir.mkdir(parents=True, exist_ok=True)
	document_source = generate_pygen(str(template_path))
	(documents_dir / "widget.py").write_text(document_source, encoding="UTF-8")

	for module_name in (
		"schemas",
		"schemas.widget",
		"routers",
		"routers.widget",
		"documents",
		"documents.widget",
	):
		monkeypatch.delitem(sys.modules, module_name, raising=False)

	monkeypatch.syspath_prepend(str(output_dir))
	importlib.invalidate_caches()

	document_module = importlib.import_module("documents.widget")
	filters = _install_fake_persistence(document_module.Widget, monkeypatch)

	routers_module = importlib.import_module("routers")

	app = fastapi.FastAPI()
	app.include_router(routers_module.router)
	install_authz(app, principal=lambda: principal, authorizer=authorizer)

	return testclient.TestClient(app), document_module.Widget, filters


def test_generated_schema_and_router_serve_crud(tmp_path, monkeypatch):
	"""End-to-end: generate real schema/router/pygen-document files, import
	them, and drive the full CRUD flow through real HTTP calls, with an
	authorizer allowing everything.
	"""
	pytest.importorskip("fastapi")
	from maas_model.authz import AllowAllAuthorizer, Principal

	client, _, _ = _serve_generated_widget(
		tmp_path, monkeypatch, AllowAllAuthorizer(), Principal("alice", ("user",))
	)

	assert client.get("/widget/").json() == []

	payload = {"id": "w1", "name": "left-flange", "mission": "S1"}
	create_response = client.post("/widget/", json=payload)
	assert create_response.status_code == 201
	item_id = create_response.headers["location"].rsplit("/", 1)[-1]
	assert item_id == "1"  # first id issued by the fake counter
	assert create_response.json() == {"document_id": "1", **payload}

	get_response = client.get(f"/widget/{item_id}")
	assert get_response.status_code == 200
	assert get_response.json()["name"] == "left-flange"

	assert len(client.get("/widget/").json()) == 1

	updated_payload = {"id": "w1", "name": "right-flange", "mission": "S1"}
	put_response = client.put(f"/widget/{item_id}", json=updated_payload)
	assert put_response.status_code == 200
	assert put_response.json()["name"] == "right-flange"

	delete_response = client.delete(f"/widget/{item_id}")
	assert delete_response.status_code == 204

	assert client.get(f"/widget/{item_id}").status_code == 404
	assert client.put(f"/widget/{item_id}", json=updated_payload).status_code == 404
	assert client.delete(f"/widget/{item_id}").status_code == 404


def test_a_free_form_resource_round_trips_the_fields_it_does_not_map(
	tmp_path, monkeypatch
):
	pytest.importorskip("fastapi")
	from maas_model.authz import AllowAllAuthorizer, Principal

	client, _, _ = _serve_generated_widget(
		tmp_path,
		monkeypatch,
		AllowAllAuthorizer(),
		Principal("alice", ("user",)),
		meta={"free_form": True},
	)
	payload = {
		"id": "w1",
		"mission": "S1",
		"class": "ODataCollectorConfiguration",
		"attr_map": {"interface_name": {"python": "lambda: 'AUXIP'"}},
	}

	created = client.post("/widget/", json=payload)

	assert created.json() == {"document_id": "1", "name": None, **payload}
	assert client.get("/widget/1").json() == created.json()

	# a replacement drops what it no longer holds
	replaced = {"id": "w1", "mission": "S1", "class": "FTPCollectorConfiguration"}
	assert client.put("/widget/1", json=replaced).status_code == 200
	assert client.get("/widget/1").json() == {
		"document_id": "1",
		"name": None,
		**replaced,
	}


def test_generated_router_lets_the_policy_decide(tmp_path, monkeypatch):
	"""End-to-end: a caller granted mission S1 only, against S1 and S2
	documents -- every route asks the authorizer, a list is filtered by the
	plan, and the permission routes say what the caller may do."""
	pytest.importorskip("fastapi")
	from maas_model.authz import Principal

	client, widget_cls, filters = _serve_generated_widget(
		tmp_path,
		monkeypatch,
		_MissionAuthorizer(),
		Principal("alice", ("user",), {"missions": ["S1"]}),
	)

	# written by someone else, a collector, not through the API
	other = document_repository.create_document(widget_cls(name="far", mission="S2"))

	create_response = client.post("/widget/", json={"name": "near", "mission": "S1"})
	assert create_response.status_code == 201
	mine = create_response.json()["document_id"]
	assert client.post("/widget/", json={"mission": "S2"}).status_code == 403

	assert client.get(f"/widget/{mine}").status_code == 200
	assert client.get(f"/widget/{other.meta.id}").status_code == 403
	assert client.get("/widget/missing").status_code == 404

	# moving my document to a mission I don't hold
	assert client.put(f"/widget/{mine}", json={"mission": "S2"}).status_code == 403
	assert client.delete(f"/widget/{other.meta.id}").status_code == 403

	assert client.get("/widget/?offset=0&size=10").status_code == 200
	assert filters == [{"terms": {"mission": ["S1"]}}]

	assert client.get("/widget/_permissions").json() == {
		"widget:create": True,
		"widget:list": True,
	}
	assert client.get(f"/widget/{other.meta.id}/permissions").json() == {
		"widget:delete": False,
		"widget:read": False,
		"widget:update": False,
	}
	assert client.get(f"/widget/{mine}/permissions").json() == {
		"widget:delete": True,
		"widget:read": True,
		"widget:update": True,
	}
	assert client.get("/widget/missing/permissions").status_code == 404

	assert client.get("/permissions").json() == {
		"widget": [
			"widget:create",
			"widget:delete",
			"widget:list",
			"widget:read",
			"widget:update",
		]
	}


# ---------------------------------------------------------------------------
# MAAS index names, package imports, Cerbos policies
# ---------------------------------------------------------------------------


def _write_template(path, properties, meta=None):
	"""a template with the given properties and optional mappings._meta"""
	mappings = {"properties": properties}
	if meta is not None:
		mappings["_meta"] = meta
	path.write_text(json.dumps({"mappings": mappings}), encoding="UTF-8")


def test_python_module_name_turns_an_index_name_into_an_identifier():
	assert python_module_name("raw-data-backup") == "raw_data_backup"
	assert python_module_name("widget") == "widget"
	assert python_module_name("2026-index") == "_2026_index"


def test_hyphenated_index_names_generate_valid_modules(tmp_path):
	"""every MAAS index is hyphenated: the modules must still import"""
	templates_dir = tmp_path / "templates"
	templates_dir.mkdir()
	_write_template(
		templates_dir / "raw-data-widget_template.json", {"name": {"type": "keyword"}}
	)

	output_dir = tmp_path / "generated"
	schema_paths, schema_errors = generate_schemas(templates_dir, output_dir)
	router_paths, router_errors = generate_routers(
		templates_dir,
		output_dir,
		available_modules={path.stem for path in schema_paths},
	)
	index_path = generate_router_index(
		[path.stem for path in router_paths], output_dir
	)

	assert not schema_errors and not router_errors
	assert [path.name for path in schema_paths] == ["raw_data_widget.py"]
	assert [path.name for path in router_paths] == ["raw_data_widget.py"]

	router_source = router_paths[0].read_text(encoding="UTF-8")
	# identifiers use the module name, the URL keeps the index name
	assert "def list_raw_data_widget(" in router_source
	assert 'prefix="/raw-data-widget"' in router_source
	assert "from schemas.raw_data_widget import Widget" in router_source

	for path in (*schema_paths, *router_paths, index_path):
		compile(path.read_text(encoding="UTF-8"), str(path), "exec")


def test_a_template_still_saying_read_only_is_served_like_any_other(tmp_path):
	"""what an index allows is the policy's business now: the former "api"
	key of mappings._meta is ignored"""
	tpl = tmp_path / "widget_template.json"
	_write_template(tpl, {"name": {"type": "keyword"}}, meta={"api": "read-only"})

	meta = ModelClassMeta(str(tpl))
	meta.load()

	source = RouterGenerator(meta).generate()

	assert '@authorize("widget:delete", WidgetDocument, kind=KIND)' in source


def test_package_and_document_module_drive_the_imports(tmp_path):
	tpl = tmp_path / "raw-data-widget_template.json"
	_write_template(tpl, {"name": {"type": "keyword"}})

	meta = ModelClassMeta(str(tpl))
	meta.load()

	source = RouterGenerator(
		meta, package="svc.generated", document_module="svc.documents"
	).generate()

	assert "from svc.generated.schemas.raw_data_widget import Widget" in source
	assert "from svc.documents import Widget as WidgetDocument" in source

	index_path = generate_router_index(
		["raw_data_widget"], tmp_path / "generated", package="svc.generated"
	)
	assert (
		"from svc.generated.routers.raw_data_widget import router as raw_data_widget_router"
		in index_path.read_text(encoding="UTF-8")
	)


def test_a_module_that_does_not_compile_is_reported_not_written(tmp_path, monkeypatch):
	templates_dir = tmp_path / "templates"
	templates_dir.mkdir()
	_write_widget_template(templates_dir / "widget_template.json")

	monkeypatch.setattr(RouterGenerator, "generate", lambda self: "def broken(:\n")

	output_dir = tmp_path / "generated"
	router_paths, router_errors = generate_routers(templates_dir, output_dir)

	assert router_paths == []
	assert [path.name for path, _ in router_errors] == ["widget_template.json"]
	assert "does not compile" in str(router_errors[0][1])
	assert not (output_dir / "routers" / "widget.py").exists()


def _policies_for(tmp_path, properties, existing_policy=None):
	"""generate the policy and attribute schema of a widget index with the
	given properties; returns (generated paths, errors, policies directory)"""
	templates_dir = tmp_path / "templates"
	templates_dir.mkdir()
	_write_template(templates_dir / "widget_template.json", properties)

	policies_dir = tmp_path / "policies"
	if existing_policy is not None:
		policies_dir.mkdir()
		(policies_dir / "widget.yaml").write_text(existing_policy, encoding="UTF-8")

	generated_paths, errors = generate_policies(templates_dir, policies_dir)
	return generated_paths, errors, policies_dir


def test_a_policy_scopes_reads_by_grants_when_the_index_maps_a_dimension(tmp_path):
	generated_paths, errors, policies_dir = _policies_for(
		tmp_path, {"mission": {"type": "keyword"}, "name": {"type": "keyword"}}
	)

	assert not errors
	assert sorted(path.relative_to(policies_dir).as_posix() for path in generated_paths) == [
		"_schemas/widget.json",
		"widget.yaml",
	]

	policy = (policies_dir / "widget.yaml").read_text(encoding="UTF-8")
	assert 'resource: "widget"' in policy
	assert "importDerivedRoles:\n    - common_roles" in policy
	assert "ref: cerbos:///widget.json" in policy
	assert 'actions: ["widget:list", "widget:read"]' in policy
	assert 'derivedRoles: ["data_permitted", "super"]' in policy
	assert "from widget_template.json" in policy
	# writes are denied: the only uncommented rule is the read one
	rules = [line for line in policy.splitlines() if line.startswith("    - actions")]
	assert rules == ['    - actions: ["widget:list", "widget:read"]']


def test_a_policy_opens_reads_to_portal_users_when_nothing_scopes_them(tmp_path):
	_, _, policies_dir = _policies_for(tmp_path, {"name": {"type": "keyword"}})

	policy = (policies_dir / "widget.yaml").read_text(encoding="UTF-8")
	assert 'derivedRoles: ["portal_user"]' in policy
	assert "data_permitted" not in policy


def test_the_attribute_schema_is_every_mapped_field_nullable(tmp_path):
	_, _, policies_dir = _policies_for(
		tmp_path,
		{
			"name": {"type": "keyword", "description": "The name"},
			"size": {"type": "long"},
			"ratio": {"type": "double"},
			"enabled": {"type": "boolean"},
			"created_at": {"type": "date"},
			"source_ip": {"type": "ip"},
		},
	)

	schema = json.loads(
		(policies_dir / "_schemas" / "widget.json").read_text(encoding="UTF-8")
	)

	assert schema["type"] == "object"
	assert schema["properties"] == {
		"name": {"type": ["string", "null"], "description": "The name"},
		"size": {"type": ["integer", "null"]},
		"ratio": {"type": ["number", "null"]},
		"enabled": {"type": ["boolean", "null"]},
		"created_at": {"type": ["string", "null"], "format": "date-time"},
		"source_ip": {"type": ["string", "null"]},
	}


def test_an_edited_policy_is_kept_and_its_schema_refreshed(tmp_path):
	generated_paths, errors, policies_dir = _policies_for(
		tmp_path, {"name": {"type": "keyword"}}, existing_policy="# edited by hand\n"
	)

	assert not errors
	assert [path.name for path in generated_paths] == ["widget.json"]
	assert (policies_dir / "widget.yaml").read_text(encoding="UTF-8") == (
		"# edited by hand\n"
	)


def test_generate_policies_skips_templates_whose_router_failed(tmp_path):
	templates_dir = tmp_path / "templates"
	templates_dir.mkdir()
	_write_widget_template(templates_dir / "widget_template.json")
	_write_nested_widget_template(templates_dir / "broken_template.json")

	generated_paths, errors = generate_policies(
		templates_dir, tmp_path / "policies", available_modules={"widget"}
	)

	assert sorted(path.name for path in generated_paths) == ["widget.json", "widget.yaml"]
	assert [path.name for path, _ in errors] == ["broken_template.json"]

