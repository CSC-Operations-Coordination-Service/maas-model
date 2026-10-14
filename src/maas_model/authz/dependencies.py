"""Where the principal and the authorizer come from.

maas-model knows neither how a service identifies its callers (a trusted
header today, a Keycloak token tomorrow) nor where its PDP runs: the service
installs both, once, on its application::

    install_authz(
        app,
        principal=current_principal,  # a FastAPI dependency -> Principal
        authorizer=CerbosAuthorizer("http://cerbos:3592"),
    )

Until it does, every protected route answers 500.
"""

from typing import Callable

from fastapi import FastAPI, HTTPException

from maas_model.authz.authorizer import Authorizer
from maas_model.authz.model import Principal


def get_principal() -> Principal:
    """The caller. Replaced by install_authz."""
    raise HTTPException(
        status_code=500,
        detail="no principal provider installed, see maas_model.authz.install_authz",
    )


def get_authorizer() -> Authorizer:
    """The decision point. Replaced by install_authz."""
    raise HTTPException(
        status_code=500,
        detail="no authorizer installed, see maas_model.authz.install_authz",
    )


def install_authz(
    app: FastAPI, principal: Callable[..., Principal], authorizer: Authorizer
) -> None:
    """Make every protected route of ``app`` resolve its caller with the
    ``principal`` dependency and ask ``authorizer``.

    Args:
        app: the application the generated routers are included in.
        principal: a FastAPI dependency returning the caller; it may itself
            depend on the request, a header, a token...
        authorizer: the decision point, a CerbosAuthorizer in production.
    """
    app.dependency_overrides[get_principal] = principal
    app.dependency_overrides[get_authorizer] = lambda: authorizer
