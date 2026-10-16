"""Focused validation tests for the WU8 property write adapter."""

from decimal import Decimal

import pytest

from api.routes.property_catalog_wu8 import _body_error, _record
from api.tenant_boundary import Agency, ClientAccount, Membership, TenantContext, User


def _context() -> TenantContext:
    return TenantContext(
        user=User("u1", "demo"), agency=Agency("ag1", "agency"),
        client_account=ClientAccount("acct1", "ag1", "account"),
        membership=Membership("u1", "ag1", "acct1", frozenset()), correlation_id="c1",
    )


def test_current_demo_rejects_unknown_keys_and_empty_required_fields():
    assert _body_error({"title": "x", "unexpected": 1}, frozenset({"title"}))
    with pytest.raises(ValueError, match="title"):
        _record(_context(), {"title": " ", "price_amount": "1"})
    with pytest.raises(ValueError, match="price_amount"):
        _record(_context(), {"title": "Casa"})


def test_current_demo_record_uses_server_tenant_and_domain_fields():
    record = _record(_context(), {"title": "Casa", "price_amount": "125000", "rooms": 3})
    assert record.client_account_id == "acct1"
    assert record.price_amount == Decimal("125000")
    assert record.rooms == 3
