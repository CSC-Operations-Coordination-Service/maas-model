"""Generic CRUD persistence for MAASDocument subclasses.

Model-agnostic: every function here is parameterized by a MAASDocument
subclass (or instance), never by a specific generated model -- there is
nothing here for fastapigen to generate per template. This module has
no Pydantic awareness (mirroring document.py itself); pairing a
persisted MAASDocument with its sibling Pydantic schema is
schema_adapter's job, not this module's.

Built entirely on MAASDocument / Document's existing API:

- list  -> Document.search() (inherited, already alias/partition-aware)
- get   -> MAASDocument.get_by_id() (already alias/partition-aware,
  unlike the base class's plain Document.get())
- create/update/delete -> ``opensearchpy.helpers.bulk fed by
  document.to_bulk_action(), NOT Document.save() / .delete():
  save() / delete() resolve their index via Index.name and never
  apply partition_index_name, so writing through them would silently
  skip partitioning. to_bulk_action() already computes the correct
  partitioned index for creates, and already resolves create/update op
  types from meta.seq_no / meta.primary_term reusing it keeps that
  logic in one place instead of duplicating it here.
"""

from typing import List, Optional, Type, TypeVar

from opensearchpy import Integer
from opensearchpy.helpers import bulk

from maas_model.document import MAASDocument

DocumentT = TypeVar("DocumentT", bound=MAASDocument)

_COUNTER_SCRIPT = {"source": "ctx._source.value += 1", "lang": "painless"}


class _IdCounter(MAASDocument):

    class Index:
        "inner class for DSL"

        name = "id-counters"

    value = Integer()


def next_id(resource: str, using: Optional[str] = None) -> int:
    """
    Atomically hand out the next integer id for ``resource``.

    Args:
        resource: the resource name (a document class's ``Index.name``) to
            generate the next id for -- each resource gets its own counter.
        using: connection alias to use (see ``Document.search``).

    Returns:
        The newly issued id.
    """
    connection = _IdCounter._get_connection(using)

    response = connection.update(
        index=_IdCounter._default_index(),
        id=resource,
        body={
            "script": _COUNTER_SCRIPT,
            "upsert": {"value": 1},
            "scripted_upsert": True,
        },
        retry_on_conflict=5,
        _source=True,
    )

    return response["get"]["_source"]["value"]


def list_documents(
    document_cls: Type[DocumentT], using: Optional[str] = None
) -> List[DocumentT]:
    """
    List documents of document_cls.

    Minimal first cut: no pagination, so OpenSearch's default page size
    (10) applies.

    Args:
        document_cls: the MAASDocument subclass to list.
        using: connection alias to search with.

    Returns:
        The matching documents.
    """
    return list(document_cls.search(using=using).execute())


def get_document(
    document_cls: Type[DocumentT], item_id: int, using: Optional[str] = None
) -> Optional[DocumentT]:
    """Fetch one document of document_cls by its resource id.

    Note: MAASDocument.get_by_id doesn't accept a connection alias, so
    using isn't honored here it always resolves on the class's
    default connection, same as calling get_by_id directly would.

    Args:
        document_cls: the MAASDocument subclass to fetch.
        item_id: the router-facing integer id.
        using: accepted for signature symmetry with the rest of this
            module; unused (see note above).

    Returns:
        The document, or None if no document has that id.
    """
    del using  # not supported by MAASDocument.get_by_id, see docstring
    return document_cls.get_by_id(str(item_id), ignore_missing_index=True)


def create_document(document: DocumentT, using: Optional[str] = None) -> DocumentT:
    """
    Persist a new document, assigning it a fresh id.

    Args:
        document: an unsaved MAASDocument instance; its ``meta.id`` is
            overwritten with the newly issued id.
        using: connection alias to use.

    Returns:
        ``document``, with ``meta.id`` set to the id it was saved under.
    """
    document_cls = type(document)
    resource = document_cls.Index.name

    document.meta.id = str(next_id(resource, using=using))

    connection = document_cls._get_connection(using)
    bulk(connection, [document.to_bulk_action(op_type="create")])

    return document


def update_document(
    document_cls: Type[DocumentT],
    item_id: int,
    document: DocumentT,
    using: Optional[str] = None,
) -> Optional[DocumentT]:
    """Replace an existing document's content.


    Args:
        document_cls: the MAASDocument subclass being updated.
        item_id: the router-facing integer id of the document to replace.
        document: the new content; its meta.id is overwritten.
        using: connection alias to use.

    Returns:
        document with its meta updated, or None if item_id
        doesn't exist (the caller/router should treat that as a 404).
    """
    existing = document_cls.get_by_id(str(item_id), ignore_missing_index=True)

    if existing is None:
        return None

    document.meta.id = str(item_id)
    document.meta.index = existing.meta.index
    document.meta.seq_no = existing.meta.seq_no
    document.meta.primary_term = existing.meta.primary_term

    connection = document_cls._get_connection(using)
    bulk(connection, [document.to_bulk_action()])

    return document


def delete_document(
    document_cls: Type[DocumentT], item_id: int, using: Optional[str] = None
) -> bool:
    """
    Delete a document by its resource id.

    Fetches the existing document first to_bulk_action needs its
    meta.index to know which (partitioned) index to delete from.

    Args:
        document_cls: the MAASDocument subclass being deleted from.
        item_id: the router-facing integer id of the document to delete.
        using: connection alias to use.

    Returns:
        True if a document was deleted, False if item_id didn't
        exist (the caller/router should treat that as a 404).
    """
    existing = document_cls.get_by_id(str(item_id), ignore_missing_index=True)

    if existing is None:
        return False

    connection = document_cls._get_connection(using)
    bulk(connection, [existing.to_bulk_action(op_type="delete")])

    return True
