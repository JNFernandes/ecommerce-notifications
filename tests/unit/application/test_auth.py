"""Tests for the principal and the access token errors."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from notifications.application.auth import Principal
from notifications.application.errors import AccessTokenUnavailableError, InvalidAccessTokenError
from notifications.domain.access import AccessDeniedError, AccessScope, Role

CUSTOMER = UUID("3fa85f64-5717-4562-b3fc-2c963f66afa6")


def test_should_scope_a_customer_principal_to_their_own_notifications() -> None:
    principal = Principal(subject="user-1", customer_id=CUSTOMER, roles=frozenset({Role.CUSTOMER}))

    assert principal.scope() == AccessScope.customer(CUSTOMER)


def test_should_scope_an_admin_principal_to_every_notification() -> None:
    principal = Principal(subject="admin-1", roles=frozenset({Role.ADMIN}))

    assert principal.scope() == AccessScope.all()


def test_should_deny_a_principal_without_roles() -> None:
    principal = Principal(subject="user-1", customer_id=CUSTOMER)

    with pytest.raises(AccessDeniedError):
        principal.scope()


def test_should_not_allow_changing_a_principal() -> None:
    principal = Principal(subject="user-1")

    with pytest.raises(ValidationError):
        principal.subject = "someone-else"  # type: ignore[misc]  # proves the model is frozen


def test_should_use_generic_messages_for_the_token_errors() -> None:
    assert str(InvalidAccessTokenError()) == "invalid or missing access token"
    assert str(AccessTokenUnavailableError()) == "authentication unavailable"
