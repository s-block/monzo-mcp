"""Stable redacted Monzo response fixtures."""

ACCOUNT = b"""{
  "id": "acc_1",
  "description": "Personal Account",
  "created": "2026-01-01T10:00:00Z",
  "type": "uk_retail",
  "owners": [{"user_id": "user_1", "preferred_name": "Test User"}]
}"""

BALANCE = b"""{
  "balance": 12345,
  "total_balance": 13345,
  "currency": "GBP",
  "spend_today": 1000,
  "provider_future_field": true
}"""

POT = b"""{
  "id": "pot_1",
  "name": "Holiday",
  "balance": 2500,
  "currency": "GBP",
  "created": "2026-01-01T10:00:00Z",
  "updated": "2026-02-01T10:00:00Z",
  "deleted": false,
  "style": "beach_ball"
}"""

TRANSACTION = b"""{
  "id": "tx_1",
  "created": "2026-02-01T10:00:00Z",
  "amount": -450,
  "currency": "GBP",
  "description": "Coffee",
  "merchant": "merch_1",
  "metadata": {"source": "test"},
  "notes": "",
  "is_load": false,
  "settled": "",
  "category": "eating_out"
}"""

EXPANDED_TRANSACTION = b"""{
  "id": "tx_2",
  "created": "2026-02-02T10:00:00Z",
  "amount": -900,
  "currency": "GBP",
  "description": "Lunch",
  "merchant": {
    "id": "merch_2",
    "name": "Cafe",
    "address": {"city": "London", "country": "GBR"}
  },
  "metadata": {},
  "notes": "",
  "is_load": false,
  "settled": "2026-02-02T10:01:00Z",
  "category": "future_category"
}"""

WEBHOOK = b"""{
  "id": "webhook_1",
  "account_id": "acc_1",
  "url": "https://example.test/monzo"
}"""

TOKEN = b"""{
  "access_token": "access-new",
  "client_id": "client_1",
  "expires_in": 21600,
  "refresh_token": "refresh-new",
  "token_type": "Bearer",
  "user_id": "user_1"
}"""
