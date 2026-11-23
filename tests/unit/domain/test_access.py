"""Tests for roles and access scopes."""

from uuid import UUID

import pytest

from notifications.domain.access import AccessDeniedError, AccessScope, Role
from notifications.domain.errors import DomainError

CUSTOMER_A = UUID("3fa85f64-5717-4562-b3fc-2c963f66afa6")
CUSTOMER_B = UUID("9b2d7c1e-4a5f-4c3b-8e6d-0f1a2b3c4d5e")


def test_should_cover_every_customer_when_the_scope_is_all() -> None:
    scope = AccessScope.all()

    assert scope.customer_id is None
    assert scope.is_all


def test_should_cover_one_customer_when_the_scope_is_a_customer() -> None:
    scope = AccessScope.customer(CUSTOMER_A)

    assert scope.customer_id == CUSTOMER_A
    assert not scope.is_all


def test_should_grant_all_when_the_caller_is_admin() -> None:
    assert AccessScope.for_caller([Role.ADMIN], None) == AccessScope.all()


def test_should_grant_all_when_the_caller_is_both_admin_and_customer() -> None:
    scope = AccessScope.for_caller({Role.CUSTOMER, Role.ADMIN}, CUSTOMER_A)

    assert scope == AccessScope.all()


def test_should_grant_the_own_customer_when_the_caller_is_a_customer_with_an_id() -> None:
    assert AccessScope.for_caller([Role.CUSTOMER], CUSTOMER_A) == AccessScope.customer(CUSTOMER_A)


def test_should_deny_when_the_caller_is_a_customer_without_a_customer_id() -> None:
    with pytest.raises(AccessDeniedError):
        AccessScope.for_caller([Role.CUSTOMER], None)


def test_should_deny_when_the_caller_has_a_customer_id_but_no_role() -> None:
    with pytest.raises(AccessDeniedError):
        AccessScope.for_caller([], CUSTOMER_A)


def test_should_raise_a_generic_domain_error_when_access_is_denied() -> None:
    with pytest.raises(DomainError, match=r"^access denied$"):
        AccessScope.for_caller([], None)


@pytest.mark.parametrize("requested", [None, CUSTOMER_A, CUSTOMER_B])
def test_should_permit_any_customer_when_the_scope_is_all(requested: UUID | None) -> None:
    assert AccessScope.all().permits_customer(requested)


@pytest.mark.parametrize("requested", [None, CUSTOMER_A])
def test_should_permit_the_own_customer_or_no_filter_when_the_scope_is_a_customer(
    requested: UUID | None,
) -> None:
    assert AccessScope.customer(CUSTOMER_A).permits_customer(requested)


def test_should_not_permit_another_customer_when_the_scope_is_a_customer() -> None:
    assert not AccessScope.customer(CUSTOMER_A).permits_customer(CUSTOMER_B)


def test_should_read_role_values_as_the_token_strings() -> None:
    assert [Role("CUSTOMER"), Role("ADMIN")] == [Role.CUSTOMER, Role.ADMIN]
