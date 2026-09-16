"""Generate FastAPI/Pydantic schemas from loaded model metadata."""

import argparse
import logging
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


class SchemaGenerator:
    """Generate a minimal Pydantic BaseModel source from model metadata."""

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
        imports: Set[str] = set()
        field_lines: List[str] = []

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

            field_lines.append(f"    {field.name}: {python_type}")

        lines: List[str] = ["from pydantic import BaseModel"]

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

        if field_lines:
            lines.extend(field_lines)
        else:
            lines.append("    pass")

        lines.append("")

        return "\n".join(lines)


ROUTER_TEMPLATE = '''
"""Generated CRUD router for __CLASS_NAME__.

Persists via maas_model.document_repository generic OpenSearch CRUD
built on MAASDocument's existing API and converts between this schema and its
sibling MAASDocument via maas_model.schema_adapter.

"""

from typing import List

from fastapi import APIRouter, HTTPException, Response

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

router = APIRouter(prefix="/__RESOURCE__", tags=["__RESOURCE__"])


@router.get("/", response_model=List[__CLASS_NAME__])
def list___RESOURCE__() -> List[__CLASS_NAME__]:
    """List all __CLASS_NAME__ resources."""
    documents = list_documents(__CLASS_NAME__Document)
    return [document_to_schema(document, __CLASS_NAME__) for document in documents]


@router.get("/{item_id}", response_model=__CLASS_NAME__)
def get___RESOURCE__(item_id: int) -> __CLASS_NAME__:
    """Get a single __CLASS_NAME__ resource by id."""
    document = get_document(__CLASS_NAME__Document, item_id)
    if document is None:
        raise HTTPException(status_code=404, detail="__CLASS_NAME__ not found")
    return document_to_schema(document, __CLASS_NAME__)


@router.post("/", response_model=__CLASS_NAME__, status_code=201)
def create___RESOURCE__(item: __CLASS_NAME__, response: Response) -> __CLASS_NAME__:
    """Create a new __CLASS_NAME__ resource."""
    document = create_document(schema_to_document(item, __CLASS_NAME__Document))
    response.headers["Location"] = f"/__RESOURCE__/{document.meta.id}"
    return document_to_schema(document, __CLASS_NAME__)


@router.put("/{item_id}", response_model=__CLASS_NAME__)
def update___RESOURCE__(item_id: int, item: __CLASS_NAME__) -> __CLASS_NAME__:
    """Replace an existing __CLASS_NAME__ resource."""
    document = update_document(
        __CLASS_NAME__Document, item_id, schema_to_document(item, __CLASS_NAME__Document)
    )
    if document is None:
        raise HTTPException(status_code=404, detail="__CLASS_NAME__ not found")
    return document_to_schema(document, __CLASS_NAME__)


@router.delete("/{item_id}", status_code=204)
def delete___RESOURCE__(item_id: int) -> Response:
    """Delete a __CLASS_NAME__ resource."""
    if not delete_document(__CLASS_NAME__Document, item_id):
        raise HTTPException(status_code=404, detail="__CLASS_NAME__ not found")
    return Response(status_code=204)
'''


class RouterGenerator:
    """
    Generate a CRUD FastAPI router source for a model.
    """

    def __init__(self, meta: ModelClassMeta):
        self.meta = meta

    def generate(self) -> str:
        """Return the generated FastAPI router source as Python code."""
        resource = self.meta.index_name
        schema_module = f"schemas.{resource}"
        document_module = f"documents.{resource}"

        return (
            ROUTER_TEMPLATE.replace("__SCHEMA_MODULE__", schema_module)
            .replace("__DOCUMENT_MODULE__", document_module)
            .replace("__CLASS_NAME__", self.meta.class_name)
            .replace("__RESOURCE__", resource)
        )


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
    module_name = template_path.name[: -len(ModelClassMeta.INDEX_SUFFIX)]
    generated_path = generated_directory / f"{module_name}.py"
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
        module_name = template_path.name[: -len(ModelClassMeta.INDEX_SUFFIX)]

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
                    lambda meta: RouterGenerator(meta).generate(),
                )
            )
        except (ValueError, OSError) as error:
            logging.error("Skipping %s: %s", template_path, error)
            errors.append((template_path, error))

    return generated_paths, errors


def main() -> int:
    """Generate Pydantic schemas and CRUD routers from MAAS index templates.

    Returns:
        0 if every template generated successfully, 1 if any were skipped.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("-d", "--directory", required=True, type=Path)
    parser.add_argument("-o", "--output", required=True, type=Path)
    arguments = parser.parse_args()

    schema_paths, schema_errors = generate_schemas(arguments.directory, arguments.output)
    router_paths, router_errors = generate_routers(
        arguments.directory,
        arguments.output,
        available_modules={path.stem for path in schema_paths},
    )

    for generated_path in (*schema_paths, *router_paths):
        logging.info("Generated %s", generated_path)

    errors = schema_errors + router_errors
    if errors:
        total = len(schema_paths) + len(router_paths) + len(errors)
        logging.error("%d of %d artifact(s) failed to generate", len(errors), total)
        return 1

    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())