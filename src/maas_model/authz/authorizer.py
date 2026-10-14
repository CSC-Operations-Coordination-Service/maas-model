"""Who decides: the Cerbos PDP, behind a small protocol.

Sync on purpose: the generated handlers are plain ``def`` that FastAPI runs in
its threadpool, so a blocking HTTP call to the PDP holds no event loop.
"""

import logging
from typing import Any, Dict, List, Optional, Protocol, Sequence

import httpx

from maas_model.authz.model import Plan, Principal, Resource

LOGGER = logging.getLogger(__name__)

# Cerbos requires at least one role; a principal without any is sent as this
# one, which no generated policy grants anything to.
ANONYMOUS_ROLE = "anonymous"


class AuthorizerError(RuntimeError):
    """The PDP could not answer: unreachable, or an unexpected response."""


class Authorizer(Protocol):
    """Fine-grained ABAC decision point."""

    def check(
        self,
        principal: Principal,
        resources: Sequence[Resource],
        actions: Sequence[str],
    ) -> List[Dict[str, bool]]:
        """Decide every action on every resource.

        Returns:
            One ``{action: allowed}`` map per resource, in order.
        """

    def plan(self, principal: Principal, kind: str, action: str) -> Plan:
        """Which documents of ``kind`` the principal may ``action``."""


class CerbosAuthorizer:
    """Calls a Cerbos PDP over its HTTP API.

    Args:
        url: the PDP's base URL, e.g. ``http://cerbos:3592``.
        client: the HTTP client to use, a new one with ``timeout`` when
            omitted.
        timeout: seconds before a PDP call gives up.
    """

    # Cerbos' default limit on resources per CheckResources request
    MAX_RESOURCES_PER_REQUEST = 50

    def __init__(
        self, url: str, client: Optional[httpx.Client] = None, timeout: float = 5.0
    ):
        self.url = url.rstrip("/")
        self.client = client or httpx.Client(timeout=timeout)

    def check(
        self,
        principal: Principal,
        resources: Sequence[Resource],
        actions: Sequence[str],
    ) -> List[Dict[str, bool]]:
        decisions: List[Dict[str, bool]] = []

        for start in range(0, len(resources), self.MAX_RESOURCES_PER_REQUEST):
            chunk = resources[start : start + self.MAX_RESOURCES_PER_REQUEST]

            response = self._post(
                "/api/check/resources",
                {
                    "principal": _principal_body(principal),
                    "resources": [
                        {
                            "actions": list(actions),
                            "resource": {
                                "kind": resource.kind,
                                "id": resource.id,
                                "attr": resource.attributes,
                            },
                        }
                        for resource in chunk
                    ],
                },
            )

            # results come back in request order, one per resource
            results = response.get("results", [])
            if len(results) != len(chunk):
                raise AuthorizerError(
                    f"Cerbos answered {len(results)} result(s) for {len(chunk)} "
                    "resource(s)"
                )

            for result in results:
                effects = result.get("actions", {})
                decisions.append(
                    {
                        action: effects.get(action) == "EFFECT_ALLOW"
                        for action in actions
                    }
                )

        return decisions

    def plan(self, principal: Principal, kind: str, action: str) -> Plan:
        response = self._post(
            "/api/plan/resources",
            {
                "action": action,
                "principal": _principal_body(principal),
                "resource": {"kind": kind},
            },
        )

        plan_filter = response.get("filter", {})

        return Plan(
            kind=plan_filter.get("kind", Plan.ALWAYS_DENIED),
            condition=plan_filter.get("condition"),
        )

    def _post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        try:
            response = self.client.post(f"{self.url}{path}", json=body)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise AuthorizerError(f"Cerbos {path} failed: {error}") from error


class AllowAllAuthorizer:
    """Allows everything. DEV/TEST ONLY: never install it in production."""

    def check(
        self,
        principal: Principal,
        resources: Sequence[Resource],
        actions: Sequence[str],
    ) -> List[Dict[str, bool]]:
        LOGGER.debug("allow-all authorizer: %s may %s", principal.id, actions)
        return [dict.fromkeys(actions, True) for _ in resources]

    def plan(self, principal: Principal, kind: str, action: str) -> Plan:
        return Plan(kind=Plan.ALWAYS_ALLOWED)


def _principal_body(principal: Principal) -> Dict[str, Any]:
    return {
        "id": principal.id,
        "roles": list(principal.roles) or [ANONYMOUS_ROLE],
        "attr": principal.attributes,
    }
