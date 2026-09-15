"""Generate FastAPI/Pydantic schemas from loaded model metadata."""

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Set, Tuple

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


def generate_schemas(
    template_directory: Path, output_directory: Path
) -> Tuple[List[Path], List[Tuple[Path, Exception]]]:
    """Generate Pydantic schema modules from templates in a directory.

    A template that fails to parse or generate (e.g. an unsupported field
    type, or a nested ``object`` field) is skipped rather than aborting the
    whole batch, so one bad template doesn't block every other one.

    Returns:
        A ``(generated_paths, errors)`` tuple: the schema files successfully
        written, and any ``(template_path, exception)`` pairs for templates
        that failed.
    """
    generated_directory = output_directory / "schemas" / "generated"
    generated_directory.mkdir(parents=True, exist_ok=True)

    generated_paths: List[Path] = []
    errors: List[Tuple[Path, Exception]] = []

    for template_path in sorted(template_directory.glob("*_template.json")):
        try:
            meta = ModelClassMeta(str(template_path))
            meta.load()

            generated_source = SchemaGenerator(meta).generate()
            module_name = template_path.name[: -len(ModelClassMeta.INDEX_SUFFIX)]
            generated_path = generated_directory / f"{module_name}.py"
            generated_path.write_text(generated_source, encoding="UTF-8")
            generated_paths.append(generated_path)
        except (ValueError, OSError) as error:
            logging.error("Skipping %s: %s", template_path, error)
            errors.append((template_path, error))

    return generated_paths, errors


def main() -> int:
    """Generate Pydantic schema modules from MAAS index templates.

    Returns:
        0 if every template generated successfully, 1 if any were skipped.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("-d", "--directory", required=True, type=Path)
    parser.add_argument("-o", "--output", required=True, type=Path)
    arguments = parser.parse_args()

    generated_paths, errors = generate_schemas(arguments.directory, arguments.output)

    for generated_path in generated_paths:
        logging.info("Generated %s", generated_path)

    if errors:
        logging.error(
            "%d of %d template(s) failed to generate",
            len(errors),
            len(generated_paths) + len(errors),
        )
        return 1

    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())