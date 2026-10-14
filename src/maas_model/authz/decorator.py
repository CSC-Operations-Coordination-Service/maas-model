"""The authorize decorator: a Cerbos check in front of a route handler.

::

    @router.get("/{item_id}", response_model=Widget)
    @authorize("widget:read", WidgetDocument)
    def get_widget(item_id: str, document: WidgetDocument) -> Widget:
        return document_to_schema(document, Widget)

The handler's parameters say what is checked, and on what:

- ``item_id`` (or the parameter named by ``id_parameter``): an item route.
  The document is loaded (404 when missing) and the action checked on its
  attributes; a ``document`` parameter receives it. A request body, if any
  (an update), is checked too, as the content the document is about to have.
- ``row_filter``: a collection route. The action is planned, and
  ``row_filter`` receives the OpenSearch filter selecting the documents the
  caller may see (None: all of them), for ``list_documents(query=...)``.
- otherwise, a Pydantic body: a create route, checked on the body.

Denied is 403, an unreachable PDP 503. FastAPI sees neither ``document`` nor
``row_filter``: the decorator fills them, and resolves the caller and the
authorizer through get_principal and get_authorizer (see install_authz).
"""

import contextlib
import dataclasses
import functools
import inspect
import logging
from typing import Any, Callable, Dict, Iterator, List, Optional, Type

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from maas_model.authz.attributes import document_attributes, schema_attributes
from maas_model.authz.authorizer import Authorizer, AuthorizerError
from maas_model.authz.dependencies import get_authorizer, get_principal
from maas_model.authz.model import Plan, Principal, Resource
from maas_model.authz.query_plan import UnsupportedPlanError, plan_to_query
from maas_model.document import MAASDocument
from maas_model.document_repository import get_document

LOGGER = logging.getLogger(__name__)

DOCUMENT_PARAMETER = "document"

ROW_FILTER_PARAMETER = "row_filter"

# the resource id of a document that does not exist yet
NEW_RESOURCE_ID = "new"

_PRINCIPAL_PARAMETER = "_authz_principal"

_AUTHORIZER_PARAMETER = "_authz_authorizer"


@dataclasses.dataclass(frozen=True)
class Requirement:
    """What a protected route checks: an action on a resource kind, on one
    document (item), on a whole collection, or on a document to create."""

    ITEM = "item"

    COLLECTION = "collection"

    CREATE = "create"

    kind: str

    action: str

    mode: str


def authorize(
    action: str,
    document_cls: Type[MAASDocument],
    kind: Optional[str] = None,
    id_parameter: str = "item_id",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Check ``action`` with Cerbos before the handler runs.

    Args:
        action: the Cerbos action, e.g. ``"raw-data-backup:read"``.
        document_cls: the MAASDocument class the route serves.
        kind: the Cerbos resource kind, ``document_cls.Index.name`` (the
            index name) by default.
        id_parameter: the handler parameter holding the document id on an
            item route.

    Raises:
        TypeError: at decoration, for an async handler or one whose
            parameters don't say what to check.
    """
    resource_kind = kind or document_cls.Index.name

    def decorate(handler: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(handler):
            raise TypeError(
                f"authorize: {handler.__name__} is async, only def handlers are "
                "supported"
            )

        signature = inspect.signature(handler, eval_str=True)
        parameters = signature.parameters
        body = _body_parameter(handler, signature)
        wants_document = DOCUMENT_PARAMETER in parameters

        if id_parameter in parameters and ROW_FILTER_PARAMETER not in parameters:
            mode = Requirement.ITEM
        elif ROW_FILTER_PARAMETER in parameters and id_parameter not in parameters:
            mode = Requirement.COLLECTION
        elif body is not None and id_parameter not in parameters:
            mode = Requirement.CREATE
        else:
            raise TypeError(
                f"authorize: {handler.__name__} needs either {id_parameter!r} (an "
                f"item), {ROW_FILTER_PARAMETER!r} (a collection) or a Pydantic body "
                "(a creation)"
            )

        if wants_document and mode != Requirement.ITEM:
            raise TypeError(
                f"authorize: {handler.__name__} asks for {DOCUMENT_PARAMETER!r} "
                f"without {id_parameter!r}"
            )

        requirement = Requirement(resource_kind, action, mode)

        @functools.wraps(handler)
        def wrapper(**kwargs: Any) -> Any:
            principal: Principal = kwargs.pop(_PRINCIPAL_PARAMETER)
            authorizer: Authorizer = kwargs.pop(_AUTHORIZER_PARAMETER)

            with authorizer_errors_as_503():
                if mode == Requirement.ITEM:
                    item_id = str(kwargs[id_parameter])
                    document = get_document(document_cls, item_id)
                    if document is None:
                        raise HTTPException(
                            status_code=404, detail=f"{document_cls.__name__} not found"
                        )

                    resources = [
                        Resource(resource_kind, item_id, document_attributes(document))
                    ]
                    if body is not None:
                        resources.append(
                            Resource(
                                resource_kind,
                                item_id,
                                schema_attributes(kwargs[body], document_cls),
                            )
                        )
                    _require(authorizer.check(principal, resources, [action]), action)

                    if wants_document:
                        kwargs[DOCUMENT_PARAMETER] = document

                elif mode == Requirement.COLLECTION:
                    kwargs[ROW_FILTER_PARAMETER] = _row_filter(
                        authorizer.plan(principal, resource_kind, action),
                        principal,
                        requirement,
                    )

                else:
                    resource = Resource(
                        resource_kind,
                        NEW_RESOURCE_ID,
                        schema_attributes(kwargs[body], document_cls),
                    )
                    _require(authorizer.check(principal, [resource], [action]), action)

            return handler(**kwargs)

        # what FastAPI sees: the handler's own parameters, minus those filled
        # here, plus the caller and the authorizer as dependencies
        exposed = [
            parameter
            for name, parameter in parameters.items()
            if name not in (DOCUMENT_PARAMETER, ROW_FILTER_PARAMETER)
        ]
        exposed += [
            inspect.Parameter(
                _PRINCIPAL_PARAMETER,
                inspect.Parameter.KEYWORD_ONLY,
                default=Depends(get_principal),
                annotation=Principal,
            ),
            inspect.Parameter(
                _AUTHORIZER_PARAMETER,
                inspect.Parameter.KEYWORD_ONLY,
                default=Depends(get_authorizer),
            ),
        ]
        wrapper.__signature__ = signature.replace(parameters=exposed)

        # shown in the OpenAPI description of the route
        wrapper.__doc__ = "\n\n".join(
            part
            for part in (
                inspect.cleandoc(handler.__doc__ or ""),
                f"Requires the Cerbos action `{action}` on `{resource_kind}`.",
            )
            if part
        )

        wrapper.__authz__ = requirement

        return wrapper

    return decorate


def requirements(router: Any) -> List[Requirement]:
    """The requirements of every protected route of a router (or app),
    included routers too."""
    return [
        route.endpoint.__authz__
        for route in _routes(router)
        if hasattr(getattr(route, "endpoint", None), "__authz__")
    ]


def _routes(router: Any) -> Iterator[Any]:
    """every route of a router, through the routers it includes: older
    FastAPI copies their routes, recent ones nest them (original_router)"""
    for route in router.routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _routes(included)
        else:
            yield route


@contextlib.contextmanager
def authorizer_errors_as_503() -> Iterator[None]:
    """Answer 503 when the PDP can't: no decision is no access."""
    try:
        yield
    except AuthorizerError as error:
        LOGGER.error("Authorization unavailable: %s", error)
        raise HTTPException(
            status_code=503, detail="authorization service unavailable"
        ) from error


def _body_parameter(
    handler: Callable[..., Any], signature: inspect.Signature
) -> Optional[str]:
    """the name of the handler's Pydantic body parameter, if it has one"""
    bodies = [
        name
        for name, parameter in signature.parameters.items()
        if isinstance(parameter.annotation, type)
        and issubclass(parameter.annotation, BaseModel)
    ]

    if len(bodies) > 1:
        raise TypeError(
            f"authorize: {handler.__name__} has several bodies {bodies}, cannot "
            "tell which one to check"
        )

    return bodies[0] if bodies else None


def _require(decisions: List[Dict[str, bool]], action: str) -> None:
    """403 unless the action is allowed on every resource checked"""
    if not decisions or not all(decision.get(action) for decision in decisions):
        raise HTTPException(status_code=403, detail=f"{action} denied")


def _row_filter(plan: Plan, principal: Principal, requirement: Requirement) -> Any:
    """the OpenSearch filter of a plan; 403 when it allows nothing, or can't
    be translated"""
    if plan.kind == Plan.ALWAYS_DENIED:
        raise HTTPException(status_code=403, detail=f"{requirement.action} denied")

    try:
        return plan_to_query(plan)
    except UnsupportedPlanError as error:
        LOGGER.error(
            "Refusing %s to %s: the %s policy's query plan has no OpenSearch "
            "translation: %s",
            requirement.action,
            principal.id,
            requirement.kind,
            error,
        )
        raise HTTPException(
            status_code=403, detail=f"{requirement.action} denied"
        ) from error
