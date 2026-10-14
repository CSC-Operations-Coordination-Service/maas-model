"""Generate FastAPI routers, Pydantic schemas and Cerbos policies from loaded
model metadata."""

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Callable, List, Optional, Set, Tuple

from maas_model.generator.meta import ModelClassMeta

OPENSEARCH_TO_PYDANTIC = {
    "text": "str",
    "keyword": "str",
    "integer": "int",
    "long": "int",
    "float": "float",
    "double": "float",
    "boolean": "bool",
    "date": "datetime",
    "ip": "IPvAnyAddress",
}

PYDANTIC_TYPE_IMPORTS = {
    "datetime": "from datetime import datetime",
    "IPvAnyAddress": "from pydantic import IPvAnyAddress",
}


def python_module_name(index_name: str) -> str:
    """The Python identifier for an index name.

    MAAS index names are hyphenated (``raw-data-backup``), which is fine in a
    URL and invalid in a module path or a function name. Every character that
    cannot appear in an identifier becomes an underscore; the URL prefix keeps
    the index name as it is.
    """
    name = re.sub(r"\W", "_", index_name)

    if not name or name[0].isdigit():
        name = f"_{name}"

    return name


def _module_path(package: str, *parts: str) -> str:
    """dotted import path, under ``package`` when one is given"""
    return ".".join(([package] if package else []) + list(parts))


# Schemas carry the OpenSearch _id under this name, since no index field holds
# it and a client needs it to call get. See schema_adapter.document_to_schema.
DOCUMENT_ID_FIELD = "document_id"


class SchemaGenerator:
    """Generate a minimal Pydantic BaseModel source from model metadata.

    Every field is optional: most documents are written by someone else -- a
    collector, an engine -- and a schema that requires a field would turn
    every document missing it into a 500 on list.

    A free-form template (mappings._meta.free_form) gets a schema that keeps
    the fields its mapping does not list (extra="allow"); any other schema
    drops them.
    """

    def __init__(self, meta: ModelClassMeta):
        self.meta = meta

    def _resolve_field_type(self, type_name: str) -> str:
        """Translate ModelClassMeta/FieldMeta type names into Python types."""
        opensearch_type = type_name.lower()

        if opensearch_type not in OPENSEARCH_TO_PYDANTIC:
            raise ValueError(f"Unsupported field type for fastapigen: {type_name}")

        return OPENSEARCH_TO_PYDANTIC[opensearch_type]

    def generate(self) -> str:
        """Return the generated schema source as Python code."""
        field_lines: List[str] = [f"    {DOCUMENT_ID_FIELD}: Optional[str] = None"]
        imports: Set[str] = {"from typing import Optional"}

        for field in self.meta.fields:
            if field.properties:
                raise ValueError(
                    "Nested object fields are not supported yet in fastapigen"
                )

            python_type = self._resolve_field_type(field.type_name)

            if python_type == "datetime":
                imports.add("from datetime import datetime")
            elif python_type == "IPvAnyAddress":
                imports.add("from pydantic import IPvAnyAddress")

            field_lines.append(f"    {field.name}: Optional[{python_type}] = None")

        if self.meta.free_form:
            lines: List[str] = ["from pydantic import BaseModel, ConfigDict"]
            field_lines.insert(0, '    model_config = ConfigDict(extra="allow")\n')
        else:
            lines = ["from pydantic import BaseModel"]

        if imports:
            lines.append("")
            lines.extend(sorted(imports))

        lines.extend(
            [
                "",
                "",
                f"class {self.meta.class_name}(BaseModel):",
            ]
        )

        lines.extend(field_lines)

        lines.append("")

        return "\n".join(lines)


ROUTER_TEMPLATE = '''
"""Generated router for __CLASS_NAME__, over the "__RESOURCE__" index.

Every route asks Cerbos first (maas_model.authz.authorize): the policy of the
"__RESOURCE__" resource kind decides who may list, read, create, update or
delete, and which documents -- not this module. Ids are the documents'
OpenSearch _id, as strings.

Persists via maas_model.document_repository and converts between this schema
and its sibling MAASDocument via maas_model.schema_adapter.
"""

from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from maas_model.authz import (
    Authorizer,
    Principal,
    RowFilter,
    authorize,
    get_authorizer,
    get_principal,
    item_permissions,
    kind_permissions,
)
from maas_model.document_repository import (
    create_document,
    delete_document,
    get_document,
    list_documents,
    update_document,
)
from maas_model.schema_adapter import document_to_schema, schema_to_document

from __SCHEMA_MODULE__ import __CLASS_NAME__
from __DOCUMENT_MODULE__ import __CLASS_NAME__ as __CLASS_NAME__Document

KIND = "__RESOURCE__"

router = APIRouter(prefix="/__RESOURCE__", tags=["__RESOURCE__"])


# declared before /{item_id}, which would take "_permissions" for an id
@router.get("/_permissions", response_model=Dict[str, bool])
def kind_permissions___FUNC__(
    principal: Principal = Depends(get_principal),
    authorizer: Authorizer = Depends(get_authorizer),
) -> Dict[str, bool]:
    """What the caller may do on __CLASS_NAME__ resources as a whole."""
    return kind_permissions(principal, authorizer, router, KIND)


@router.get("/", response_model=List[__CLASS_NAME__])
@authorize("__RESOURCE__:list", __CLASS_NAME__Document, kind=KIND)
def list___FUNC__(
    row_filter: RowFilter,
    offset: int = Query(0, ge=0, le=9000),
    size: int = Query(50, ge=1, le=1000),
) -> List[__CLASS_NAME__]:
    """List the __CLASS_NAME__ resources the caller may see, one page at a time.

    offset + size stays under OpenSearch's default max_result_window (10000).
    """
    documents = list_documents(
        __CLASS_NAME__Document, offset=offset, size=size, query=row_filter
    )
    return [document_to_schema(document, __CLASS_NAME__) for document in documents]


@router.get("/{item_id}", response_model=__CLASS_NAME__)
@authorize("__RESOURCE__:read", __CLASS_NAME__Document, kind=KIND)
def get___FUNC__(item_id: str, document: __CLASS_NAME__Document) -> __CLASS_NAME__:
    """Get a single __CLASS_NAME__ resource by its document id."""
    return document_to_schema(document, __CLASS_NAME__)


@router.get("/{item_id}/permissions", response_model=Dict[str, bool])
def item_permissions___FUNC__(
    item_id: str,
    principal: Principal = Depends(get_principal),
    authorizer: Authorizer = Depends(get_authorizer),
) -> Dict[str, bool]:
    """What the caller may do on one __CLASS_NAME__ resource."""
    document = get_document(__CLASS_NAME__Document, item_id)
    if document is None:
        raise HTTPException(status_code=404, detail="__CLASS_NAME__ not found")
    return item_permissions(principal, authorizer, router, KIND, document)


@router.post("/", response_model=__CLASS_NAME__, status_code=201)
@authorize("__RESOURCE__:create", __CLASS_NAME__Document, kind=KIND)
def create___FUNC__(item: __CLASS_NAME__, response: Response) -> __CLASS_NAME__:
    """Create a new __CLASS_NAME__ resource."""
    document = create_document(schema_to_document(item, __CLASS_NAME__Document))
    response.headers["Location"] = f"/__RESOURCE__/{document.meta.id}"
    return document_to_schema(document, __CLASS_NAME__)


@router.put("/{item_id}", response_model=__CLASS_NAME__)
@authorize("__RESOURCE__:update", __CLASS_NAME__Document, kind=KIND)
def update___FUNC__(item_id: str, item: __CLASS_NAME__) -> __CLASS_NAME__:
    """Replace an existing __CLASS_NAME__ resource.

    Checked on both the stored document and the new content.
    """
    document = update_document(
        __CLASS_NAME__Document, item_id, schema_to_document(item, __CLASS_NAME__Document)
    )
    if document is None:
        raise HTTPException(status_code=404, detail="__CLASS_NAME__ not found")
    return document_to_schema(document, __CLASS_NAME__)


@router.delete("/{item_id}", status_code=204)
@authorize("__RESOURCE__:delete", __CLASS_NAME__Document, kind=KIND)
def delete___FUNC__(item_id: str) -> Response:
    """Delete a __CLASS_NAME__ resource."""
    if not delete_document(__CLASS_NAME__Document, item_id):
        raise HTTPException(status_code=404, detail="__CLASS_NAME__ not found")
    return Response(status_code=204)
'''


class RouterGenerator:
    """
    Generate a FastAPI router source for a model: list, get, create, update
    and delete, each checked by Cerbos (see maas_model.authz), plus the
    caller's permissions on the index and on one document.

    Args:
        meta: the loaded template metadata.
        package: dotted package the generated schemas/, routers/ and
            documents/ live in, e.g. "my_service.generated". Empty (the
            default) imports them as top-level packages, which needs the
            output directory on sys.path.
        document_module: dotted module holding every MAASDocument class,
            e.g. a single pygen output. Empty (the default) imports each
            class from documents.<model>.
    """

    def __init__(
        self, meta: ModelClassMeta, package: str = "", document_module: str = ""
    ):
        self.meta = meta
        self.package = package
        self.document_module = document_module

    def generate(self) -> str:
        """Return the generated FastAPI router source as Python code."""
        resource = self.meta.index_name
        module = python_module_name(resource)
        schema_module = _module_path(self.package, "schemas", module)
        document_module = self.document_module or _module_path(
            self.package, "documents", module
        )

        return (
            ROUTER_TEMPLATE.replace("__SCHEMA_MODULE__", schema_module)
            .replace("__DOCUMENT_MODULE__", document_module)
            .replace("__CLASS_NAME__", self.meta.class_name)
            .replace("__FUNC__", module)
            .replace("__RESOURCE__", resource)
        )


ROUTERS_INIT_TEMPLATE = '''"""Generated top-level router aggregating every resource router."""

from typing import Dict, List

from fastapi import APIRouter, Depends

from maas_model.authz import Principal, get_principal, permission_catalog

__IMPORTS__

router = APIRouter()

__INCLUDES__


@router.get("/permissions", response_model=Dict[str, List[str]], tags=["permissions"])
def permissions(
    principal: Principal = Depends(get_principal),
) -> Dict[str, List[str]]:
    """Every resource kind and the Cerbos actions its routes check, for
    whoever writes the policies."""
    del principal  # any identified caller may read the catalog
    return permission_catalog(router)
'''


def _load_and_render(
    template_path: Path,
    generated_directory: Path,
    render: Callable[[ModelClassMeta], str],
) -> Path:
    """
    Load a template's metadata, render it, and write the module.
    """
    meta = ModelClassMeta(str(template_path))
    meta.load()

    generated_source = render(meta)
    generated_path = generated_directory / f"{python_module_name(meta.index_name)}.py"

    # Refuse to write a module that does not compile, rather than leave it for
    # the import to find: the caller records this like any other failure.
    try:
        compile(generated_source, str(generated_path), "exec")
    except SyntaxError as error:
        raise ValueError(f"generated source does not compile: {error}") from error

    generated_path.write_text(generated_source, encoding="UTF-8")
    return generated_path


def generate_schemas(
    template_directory: Path, output_directory: Path
) -> Tuple[List[Path], List[Tuple[Path, Exception]]]:
    """Generate Pydantic schema modules from templates in a directory.

    A template that fails to parse or generate (e.g. an unsupported field
    type, or a nested object field) is skipped rather than aborting the
    whole batch, so one bad template doesn't block every other one.

    Returns:
        A (generated_paths, errors) tuple: the schema files successfully
        written, and any (template_path, exception) pairs for templates
        that failed.
    """
    generated_directory = output_directory / "schemas"
    generated_directory.mkdir(parents=True, exist_ok=True)

    generated_paths: List[Path] = []
    errors: List[Tuple[Path, Exception]] = []

    for template_path in sorted(template_directory.glob("*_template.json")):
        try:
            generated_paths.append(
                _load_and_render(
                    template_path,
                    generated_directory,
                    lambda meta: SchemaGenerator(meta).generate(),
                )
            )
        except (ValueError, OSError) as error:
            logging.error("Skipping %s: %s", template_path, error)
            errors.append((template_path, error))

    return generated_paths, errors


def generate_routers(
    template_directory: Path,
    output_directory: Path,
    available_modules: Optional[Set[str]] = None,
    package: str = "",
    document_module: str = "",
) -> Tuple[List[Path], List[Tuple[Path, Exception]]]:
    """
    Generate FastAPI CRUD router modules from templates in a directory.

    Written alongside the schemas, at <output>/routers/<model>.py.

    Each router also imports a same-named MAASDocument class from
    documents.<model> for persistence (see ``RouterGenerator``) -- that
    module is NOT generated by this function (or by fastapigen at all); it
    must come from pygen, placed at that path separately, for the
    generated router to actually run.

    Returns:
        Same (generated_paths, errors) shape as generate_schemas.
    """
    generated_directory = output_directory / "routers"
    generated_directory.mkdir(parents=True, exist_ok=True)

    generated_paths: List[Path] = []
    errors: List[Tuple[Path, Exception]] = []

    for template_path in sorted(template_directory.glob("*_template.json")):
        module_name = python_module_name(
            template_path.name[: -len(ModelClassMeta.INDEX_SUFFIX)]
        )

        if available_modules is not None and module_name not in available_modules:
            error = RuntimeError("schema generation failed; router not generated")
            logging.error("Skipping router for %s: %s", template_path, error)
            errors.append((template_path, error))
            continue

        try:
            generated_paths.append(
                _load_and_render(
                    template_path,
                    generated_directory,
                    lambda meta: RouterGenerator(
                        meta, package=package, document_module=document_module
                    ).generate(),
                )
            )
        except (ValueError, OSError) as error:
            logging.error("Skipping %s: %s", template_path, error)
            errors.append((template_path, error))

    return generated_paths, errors


def generate_router_index(
    resources: List[str], output_directory: Path, package: str = ""
) -> Path:
    """Generate routers/__init__.py, aggregating every resource router
    into one top-level APIRouter.

    ``resources`` are module names, i.e. the stems of the generated router
    files, already valid identifiers.
    """
    generated_directory = output_directory / "routers"
    generated_directory.mkdir(parents=True, exist_ok=True)

    imports = "\n".join(
        f"from {_module_path(package, 'routers', resource)} "
        f"import router as {resource}_router"
        for resource in resources
    )
    includes = "\n".join(
        f"router.include_router({resource}_router)" for resource in resources
    )

    generated_source = ROUTERS_INIT_TEMPLATE.replace("__IMPORTS__", imports).replace(
        "__INCLUDES__", includes
    )

    generated_path = generated_directory / "__init__.py"
    generated_path.write_text(generated_source, encoding="UTF-8")
    return generated_path


# The fields ocs-portal's data_permitted derived role scopes documents by: an
# index mapping any of them gets its reads scoped by the caller's grants.
DIMENSION_FIELDS = ("mission", "data_type", "phase")

OPENSEARCH_TO_JSON_SCHEMA = {
    "text": {"type": "string"},
    "keyword": {"type": "string"},
    "integer": {"type": "integer"},
    "long": {"type": "integer"},
    "float": {"type": "number"},
    "double": {"type": "number"},
    "boolean": {"type": "boolean"},
    "date": {"type": "string", "format": "date-time"},
    "ip": {"type": "string"},
}

POLICY_TEMPLATE = """\
# Cerbos resource policy for the "__RESOURCE__" index, generated by fastapigen
# from __TEMPLATE__ as a starting point: edit it, fastapigen never overwrites
# it. The attribute schema it references, _schemas/__RESOURCE__.json, is
# regenerated from the mappings every time.
#
# One action per generated route:
#   __RESOURCE__:list    GET    /__RESOURCE__/       filtered by the query plan
#   __RESOURCE__:read    GET    /__RESOURCE__/{id}
#   __RESOURCE__:create  POST   /__RESOURCE__/
#   __RESOURCE__:update  PUT    /__RESOURCE__/{id}   on the stored and the new content
#   __RESOURCE__:delete  DELETE /__RESOURCE__/{id}
#
# Writing conditions:
#   - request.resource.attr holds every mapped field, null when a document has
#     none: guard with R.attr.x != null, never has(R.attr.x) -- the query
#     planner folds has() to true, so a list would disagree with a get.
#   - a list translates the query plan into an OpenSearch filter: compare
#     keyword, numeric, date or boolean fields with ==, !=, <, <=, >, >=, in,
#     &&, || and !; anything else denies the list.
---
apiVersion: api.cerbos.dev/v1
resourcePolicy:
  version: default
  resource: "__RESOURCE__"
  importDerivedRoles:
    - common_roles
  schemas:
    resourceSchema:
      ref: cerbos:///__RESOURCE__.json
  rules:
__READ_RULE__
    # No create, update or delete rule: Cerbos denies what no rule allows, so
    # the index stays read-only until a rule opens it. For instance, let
    # operators update the documents assigned to them:
    #
    # - actions: ["__RESOURCE__:update"]
    #   effect: EFFECT_ALLOW
    #   roles: ["operator"]
    #   condition:
    #     match:
    #       expr: R.attr.assignee == P.id
"""

SCOPED_READ_RULE = """\
    # Read: the documents of the missions, data types and phases the caller
    # holds a grant for (data_permitted), every document for super_admin. A
    # document whose dimensions are null matches no grant: open those with a
    # rule on, e.g., R.attr.mission == null.
    - actions: ["__RESOURCE__:list", "__RESOURCE__:read"]
      effect: EFFECT_ALLOW
      derivedRoles: ["data_permitted", "super"]
"""

UNSCOPED_READ_RULE = """\
    # Read: any portal persona -- the index maps no mission, data_type or
    # phase field to scope its documents by.
    - actions: ["__RESOURCE__:list", "__RESOURCE__:read"]
      effect: EFFECT_ALLOW
      derivedRoles: ["portal_user"]
"""


class PolicyGenerator:
    """Generate the Cerbos resource policy skeleton of an index, and the JSON
    schema of the resource attributes its routes send.

    The policy imports ocs-portal's common_roles derived roles: reads are
    scoped by the caller's grants when the index maps a dimension field
    (see DIMENSION_FIELDS), open to any portal persona otherwise; nothing
    else is allowed.
    """

    def __init__(self, meta: ModelClassMeta, template_name: str):
        self.meta = meta
        self.template_name = template_name

    def attribute_schema(self) -> str:
        """Return the resource attribute JSON schema: every mapped field,
        nullable, since a document may have none."""
        properties = {}

        for field in self.meta.fields:
            if field.properties:
                raise ValueError(
                    "Nested object fields are not supported yet in fastapigen"
                )

            opensearch_type = field.type_name.lower()
            if opensearch_type not in OPENSEARCH_TO_JSON_SCHEMA:
                raise ValueError(
                    f"Unsupported field type for fastapigen: {field.type_name}"
                )

            schema = dict(OPENSEARCH_TO_JSON_SCHEMA[opensearch_type])
            schema["type"] = [schema["type"], "null"]
            if field.description:
                schema["description"] = field.description

            properties[field.name] = schema

        return (
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "title": self.meta.index_name,
                    "description": f"Attributes of a {self.meta.index_name} "
                    f"document, generated by fastapigen from {self.template_name}",
                    "type": "object",
                    "properties": properties,
                },
                indent=2,
            )
            + "\n"
        )

    def policy(self) -> str:
        """Return the resource policy skeleton, as YAML."""
        scoped = any(name in DIMENSION_FIELDS for name in self.meta.field_names)

        return (
            POLICY_TEMPLATE.replace(
                "__READ_RULE__", SCOPED_READ_RULE if scoped else UNSCOPED_READ_RULE
            )
            .replace("__TEMPLATE__", self.template_name)
            .replace("__RESOURCE__", self.meta.index_name)
        )


def generate_policies(
    template_directory: Path,
    policies_directory: Path,
    available_modules: Optional[Set[str]] = None,
) -> Tuple[List[Path], List[Tuple[Path, Exception]]]:
    """Generate a Cerbos policy skeleton and attribute schema per template.

    Writes <policies>/_schemas/<index>.json every time, and
    <policies>/<index>.yaml only when it does not exist yet: the policy is
    meant to be edited by hand, the schema follows the mappings.

    Returns:
        Same (generated_paths, errors) shape as generate_schemas; a policy
        kept as it was is not in generated_paths.
    """
    schemas_directory = policies_directory / "_schemas"
    schemas_directory.mkdir(parents=True, exist_ok=True)

    generated_paths: List[Path] = []
    errors: List[Tuple[Path, Exception]] = []

    for template_path in sorted(template_directory.glob("*_template.json")):
        module_name = python_module_name(
            template_path.name[: -len(ModelClassMeta.INDEX_SUFFIX)]
        )

        if available_modules is not None and module_name not in available_modules:
            error = RuntimeError("router generation failed; policy not generated")
            logging.error("Skipping policy for %s: %s", template_path, error)
            errors.append((template_path, error))
            continue

        try:
            meta = ModelClassMeta(str(template_path))
            meta.load()

            generator = PolicyGenerator(meta, template_path.name)
            attribute_schema = generator.attribute_schema()

            schema_path = schemas_directory / f"{meta.index_name}.json"
            schema_path.write_text(attribute_schema, encoding="UTF-8")
            generated_paths.append(schema_path)

            policy_path = policies_directory / f"{meta.index_name}.yaml"
            if policy_path.exists():
                logging.info("Keeping %s as it is", policy_path)
            else:
                policy_path.write_text(generator.policy(), encoding="UTF-8")
                generated_paths.append(policy_path)
        except (ValueError, OSError) as error:
            logging.error("Skipping %s: %s", template_path, error)
            errors.append((template_path, error))

    return generated_paths, errors


def main() -> int:
    """Generate Pydantic schemas, routers, the top-level router index
    (routers/__init__.py) and, with --policies, the Cerbos policy skeletons
    from MAAS index templates.

    Returns:
        0 if every template generated successfully, 1 if any were skipped.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("-d", "--directory", required=True, type=Path)
    parser.add_argument("-o", "--output", required=True, type=Path)
    parser.add_argument(
        "-p",
        "--package",
        default="",
        help="dotted package the output directory is importable as, e.g. "
        "my_service.generated (default: top-level schemas/routers/documents)",
    )
    parser.add_argument(
        "--document-module",
        default="",
        help="dotted module holding every MAASDocument class, e.g. a single "
        "pygen output (default: documents.<model> under --package)",
    )
    parser.add_argument(
        "--policies",
        type=Path,
        help="directory to write a Cerbos policy skeleton (kept when it exists) "
        "and an attribute schema (_schemas/) per index into",
    )
    arguments = parser.parse_args()

    schema_paths, schema_errors = generate_schemas(arguments.directory, arguments.output)
    router_paths, router_errors = generate_routers(
        arguments.directory,
        arguments.output,
        available_modules={path.stem for path in schema_paths},
        package=arguments.package,
        document_module=arguments.document_module,
    )
    router_index_path = generate_router_index(
        [path.stem for path in router_paths], arguments.output, arguments.package
    )

    policy_paths: List[Path] = []
    policy_errors: List[Tuple[Path, Exception]] = []
    if arguments.policies:
        policy_paths, policy_errors = generate_policies(
            arguments.directory,
            arguments.policies,
            available_modules={path.stem for path in router_paths},
        )

    for generated_path in (
        *schema_paths,
        *router_paths,
        router_index_path,
        *policy_paths,
    ):
        logging.info("Generated %s", generated_path)

    errors = schema_errors + router_errors + policy_errors
    if errors:
        total = len(schema_paths) + len(router_paths) + len(errors)
        logging.error("%d of %d artifact(s) failed to generate", len(errors), total)
        return 1

    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())