"""Payment agent: the case-specified mock payment function plus its wrapper."""

from __future__ import annotations


def mock_payment(vendor, amount):
    """Mock payment API, exactly as specified in the case README."""
    print(f"Paid {amount} to {vendor}")
    return {"status": "success"}


def execute_payment(vendor: str | None, amount_usd: float | None) -> dict:
    """Pay via the mock API and return an enriched payment record."""
    vendor_name = vendor or "UNKNOWN VENDOR"
    amount = round(amount_usd, 2) if amount_usd is not None else 0.0
    result = mock_payment(vendor_name, amount)
    return {**result, "vendor": vendor_name, "amount_usd": amount}
