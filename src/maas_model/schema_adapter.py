"""
Convert between fastapigen's Pydantic schemas and pygen's MAASDocument
persistence models.
"""

from typing import Optional, Type, TypeVar

from pydantic import BaseModel

from maas_model.document import MAASDocument

SchemaT = TypeVar("SchemaT", bound=BaseModel)

DocumentT = TypeVar("DocumentT", bound=MAASDocument)


def document_to_schema(document: MAASDocument, schema_cls: Type[SchemaT]) -> SchemaT:
    """Build a Pydantic schema instance from a MAASDocument.

    Args:
        document: a loaded MAASDocument instance (e.g. from get_by_id).
        schema_cls: the Pydantic model class to build.

    Returns:
        A populated schema_cls instance.
    """
    field_names = document._INITIAL_FIELDS.keys() & schema_cls.model_fields.keys()

    data = {name: getattr(document, name) for name in field_names}

    # a free-form schema (extra="allow") also gets the fields no mapping lists
    if schema_cls.model_config.get("extra") == "allow":
        data.update(
            (name, value)
            for name, value in document.to_dict().items()
            if name not in document._INITIAL_FIELDS
        )

    # fastapigen's read-only schemas carry the OpenSearch _id, which no index
    # field holds, so a client can get what it listed. An index field of that
    # name wins.
    if "document_id" in schema_cls.model_fields and "document_id" not in data:
        data["document_id"] = getattr(document.meta, "id", None)

    return schema_cls(**data)


def schema_to_document(
    schema: BaseModel,
    document_cls: Type[DocumentT],
    document_id: Optional[str] = None,
) -> DocumentT:
    """Build a MAASDocument instance from a Pydantic schema.

    Args:
        schema: a validated Pydantic model instance (e.g. a request body).
        document_cls: the MAASDocument subclass to build.
        document_id: the document's OpenSearch _id, if known (e.g. when
            updating an existing document). Omit when creating a new one.

    Returns:
        A populated document_cls instance.
    """
    field_names = document_cls._INITIAL_FIELDS.keys() & type(schema).model_fields.keys()

    data = {
        name: value
        for name, value in schema.model_dump().items()
        if name in field_names
    }

    # and gives them back: the fields a free-form schema holds beyond its own
    data.update(schema.model_extra or {})

    meta = {"id": document_id} if document_id is not None else {}

    return document_cls(meta=meta, **data)
