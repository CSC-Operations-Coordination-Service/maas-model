"""Documents and request bodies as Cerbos resource attributes.

Every mapped field (``_INITIAL_FIELDS``) is always present, null when the
document has none. A policy then guards with ``R.attr.x != null``, which a
check and a query plan read the same way -- ``has(R.attr.x)`` would not: the
planner folds it to true. Values are made JSON-safe on the way.
"""

import datetime
from typing import Any, Dict, Mapping, Type

from maas_model.document import MAASDocument


def document_attributes(document: MAASDocument) -> Dict[str, Any]:
    """The attributes of a stored document."""
    return {
        name: _attribute(getattr(document, name, None))
        for name in type(document)._INITIAL_FIELDS
    }


def schema_attributes(item: Any, document_cls: Type[MAASDocument]) -> Dict[str, Any]:
    """The attributes of a request body (a Pydantic model), as the document
    ``document_cls`` it will be stored as."""
    data = item.model_dump(mode="json")

    return {name: _attribute(data.get(name)) for name in document_cls._INITIAL_FIELDS}


def _attribute(value: Any) -> Any:
    """a field's value as an attribute: an empty array or object is null, as
    it is to OpenSearch's exists, which a plan's ``!= null`` becomes"""
    value = json_safe(value)

    return None if value in ([], {}) else value


def json_safe(value: Any) -> Any:
    """``value`` as JSON: datetimes as ISO 8601, inner documents as objects,
    anything else unknown as its string."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value

    if isinstance(value, datetime.datetime):
        return value.isoformat().replace("+00:00", "Z")

    if isinstance(value, datetime.date):
        return value.isoformat()

    if hasattr(value, "to_dict"):
        value = value.to_dict()

    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}

    # lists, tuples and opensearch-py's AttrList
    if hasattr(value, "__iter__") and not isinstance(value, (bytes, bytearray)):
        return [json_safe(item) for item in value]

    return str(value)
