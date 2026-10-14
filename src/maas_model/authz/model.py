"""The values an authorization check is made of.

Same shape as ocs-portal's ``ocs.shared.domain.authz``, so a principal built
from the same Keycloak token means the same thing to both.
"""

import dataclasses
from typing import Any, Dict, Optional, Tuple


@dataclasses.dataclass(frozen=True)
class Principal:
    """The caller: an id, its roles (Keycloak personas such as ``user`` or
    ``operator``) and its attributes (``grants``, ...)."""

    id: str

    roles: Tuple[str, ...] = ()

    attributes: Dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class Resource:
    """What the caller acts on: a kind (an index name), an id (the document's
    OpenSearch _id) and the attributes the policy conditions read."""

    kind: str

    id: str

    attributes: Dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class Plan:
    """A PlanResources answer: which documents of a kind the caller may act on.

    ``condition`` is Cerbos' expression tree, set only when ``kind`` is
    CONDITIONAL. See query_plan.plan_to_query.
    """

    ALWAYS_ALLOWED = "KIND_ALWAYS_ALLOWED"

    ALWAYS_DENIED = "KIND_ALWAYS_DENIED"

    CONDITIONAL = "KIND_CONDITIONAL"

    kind: str

    condition: Optional[Dict[str, Any]] = None
