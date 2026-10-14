"""What the caller may do, for a UI to show or hide its affordances.

The actions come from the routes themselves -- each @authorize route carries
its requirement -- so these answers can't drift from what the routes check.
"""

from typing import Any, Dict, List, Set

from maas_model.authz.attributes import document_attributes
from maas_model.authz.authorizer import Authorizer
from maas_model.authz.decorator import (
    Requirement,
    authorizer_errors_as_503,
    requirements,
)
from maas_model.authz.model import Plan, Principal, Resource
from maas_model.document import MAASDocument


def kind_permissions(
    principal: Principal, authorizer: Authorizer, router: Any, kind: str
) -> Dict[str, bool]:
    """The caller's collection and create actions on ``kind``, e.g.
    ``{"widget:create": false, "widget:list": true}``.

    An action is true unless the caller may do it on no document at all: a
    list filtered down to the caller's missions is still a list.
    """
    actions = _actions(router, kind, lambda mode: mode != Requirement.ITEM)

    with authorizer_errors_as_503():
        return {
            action: authorizer.plan(principal, kind, action).kind != Plan.ALWAYS_DENIED
            for action in actions
        }


def item_permissions(
    principal: Principal,
    authorizer: Authorizer,
    router: Any,
    kind: str,
    document: MAASDocument,
) -> Dict[str, bool]:
    """The caller's item actions on one document, e.g.
    ``{"widget:delete": false, "widget:read": true, "widget:update": true}``.
    One CheckResources call."""
    actions = _actions(router, kind, lambda mode: mode == Requirement.ITEM)

    if not actions:
        return {}

    resource = Resource(kind, str(document.meta.id), document_attributes(document))

    with authorizer_errors_as_503():
        [decisions] = authorizer.check(principal, [resource], actions)

    return decisions


def permission_catalog(router: Any) -> Dict[str, List[str]]:
    """Every resource kind and the actions its routes check, for whoever
    writes the policies: ``{"widget": ["widget:create", ...]}``."""
    catalog: Dict[str, Set[str]] = {}

    for requirement in requirements(router):
        catalog.setdefault(requirement.kind, set()).add(requirement.action)

    return {kind: sorted(actions) for kind, actions in sorted(catalog.items())}


def _actions(router: Any, kind: str, mode_matches: Any) -> List[str]:
    return sorted(
        {
            requirement.action
            for requirement in requirements(router)
            if requirement.kind == kind and mode_matches(requirement.mode)
        }
    )
