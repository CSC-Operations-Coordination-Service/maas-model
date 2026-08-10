"""Generate FastAPI/Pydantic schemas from loaded model metadata."""

from typing import List, Set

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
    "ip": "IPvAnyAddress"
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