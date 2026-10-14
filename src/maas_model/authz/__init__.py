"""Cerbos ABAC for the routers fastapigen generates.

Every generated route is decorated with ``authorize``: the Cerbos policy of
the index -- not the router -- decides who may list, read, create, update or
delete, and which documents. A service wires its caller and its PDP once::

    from maas_model.authz import CerbosAuthorizer, install_authz

    install_authz(app, principal=current_principal, authorizer=CerbosAuthorizer(url))

Needs the ``api`` extra (fastapi, pydantic, httpx).
"""

from maas_model.authz.attributes import document_attributes, schema_attributes
from maas_model.authz.authorizer import (
    AllowAllAuthorizer,
    Authorizer,
    AuthorizerError,
    CerbosAuthorizer,
)
from maas_model.authz.decorator import Requirement, authorize, requirements
from maas_model.authz.dependencies import get_authorizer, get_principal, install_authz
from maas_model.authz.model import Plan, Principal, Resource
from maas_model.authz.permissions import (
    item_permissions,
    kind_permissions,
    permission_catalog,
)
from maas_model.authz.query_plan import RowFilter, UnsupportedPlanError, plan_to_query

__all__ = [
    "AllowAllAuthorizer",
    "Authorizer",
    "AuthorizerError",
    "CerbosAuthorizer",
    "Plan",
    "Principal",
    "Requirement",
    "Resource",
    "RowFilter",
    "UnsupportedPlanError",
    "authorize",
    "document_attributes",
    "get_authorizer",
    "get_principal",
    "install_authz",
    "item_permissions",
    "kind_permissions",
    "permission_catalog",
    "plan_to_query",
    "requirements",
    "schema_attributes",
]
