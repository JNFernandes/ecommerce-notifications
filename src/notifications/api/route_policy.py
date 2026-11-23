"""Startup check that every API route declares its access: public, or the roles it allows.

Default deny: a route added without a declaration stops the service at startup instead of being
served without authentication. FastAPI's own docs routes (``/docs``, ``/redoc``,
``/openapi.json``) are not ``APIRoute``s and are public by design.
"""

from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from notifications.api.auth import RoleRequirement, public_access


class RoutePolicyError(Exception):
    """A route declares neither public access nor roles, or both."""


def _calls(dependant: Dependant) -> Iterator[Any]:  # Any: FastAPI types dependency calls loosely
    for dependency in dependant.dependencies:
        yield dependency.call
        yield from _calls(dependency)


def assert_route_policies(app: FastAPI) -> None:
    """Check every ``APIRoute`` of ``app``.

    Raises:
        RoutePolicyError: for the first route that declares neither ``PUBLIC`` nor
            ``require_roles``, or declares both.
    """
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        calls = list(_calls(route.dependant))
        public = any(call is public_access for call in calls)
        roles = any(isinstance(call, RoleRequirement) for call in calls)
        if public == roles:
            problem = (
                "both public access and roles" if public else "neither public access nor roles"
            )
            methods = ",".join(sorted(route.methods or ()))
            raise RoutePolicyError(f"route {methods} {route.path} declares {problem}")
