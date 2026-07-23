"""Pydantic boundary tests."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr, ValidationError

from monzo_mcp.client import Merchant, OAuthToken, RetryPolicy, Transaction
from tests.client.helpers import EXPANDED_TRANSACTION, TRANSACTION


def test_transaction_normalizes_empty_settled_and_preserves_extras() -> None:
    payload = TRANSACTION[:-1] + b', "new_provider_field": 42}'

    transaction = Transaction.model_validate_json(payload)

    assert transaction.settled is None
    assert transaction.merchant == "merch_1"
    assert transaction.model_extra == {"new_provider_field": 42}


def test_transaction_accepts_expanded_merchant_and_future_category() -> None:
    transaction = Transaction.model_validate_json(EXPANDED_TRANSACTION)

    assert isinstance(transaction.merchant, Merchant)
    assert transaction.merchant.address is not None
    assert transaction.merchant.address.city == "London"
    assert transaction.category == "future_category"


def test_transaction_rejects_malformed_required_fields() -> None:
    with pytest.raises(ValidationError):
        Transaction.model_validate_json(TRANSACTION.replace(b'"GBP"', b'"gb"'))


def test_oauth_token_masks_secrets_and_checks_expiry_skew() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    token = OAuthToken(
        access_token=SecretStr("access-sensitive"),
        refresh_token=SecretStr("refresh-sensitive"),
        expires_at=now + timedelta(seconds=20),
    )

    rendered = repr(token)
    assert "access-sensitive" not in rendered
    assert "refresh-sensitive" not in rendered
    assert token.is_expiring(at=now, skew=timedelta(seconds=30))


def test_retry_policy_rejects_reversed_delay_range() -> None:
    with pytest.raises(ValidationError):
        RetryPolicy(base_delay_seconds=2.0, max_delay_seconds=1.0)
