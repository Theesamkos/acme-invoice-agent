from datetime import date

from invoice_agent.models import InvoiceData, LineItem
from invoice_agent.tools.fraud import score_fraud
from invoice_agent.tools.math_check import check_math
from invoice_agent.tools.normalize import match_item, parse_date, to_usd

INVENTORY = ["WidgetA", "WidgetB", "GadgetX", "FakeItem"]


class TestItemMatching:
    def test_exact(self):
        assert match_item("WidgetA", INVENTORY) == ("WidgetA", "exact")

    def test_spacing_and_case_normalize(self):
        assert match_item("Widget A", INVENTORY) == ("WidgetA", "normalized")
        assert match_item("gadget x", INVENTORY) == ("GadgetX", "normalized")

    def test_sibling_sku_must_not_fuzzy_match(self):
        # WidgetC is a real unknown product; matching it to WidgetA would
        # validate a nonexistent item. This is the FUZZY_THRESHOLD rationale.
        assert match_item("WidgetC", INVENTORY) == (None, "unknown")

    def test_unknown_items(self):
        assert match_item("SuperGizmo", INVENTORY) == (None, "unknown")
        assert match_item("MegaSprocket", INVENTORY) == (None, "unknown")


class TestDates:
    def test_iso(self):
        assert parse_date("2026-01-15") == date(2026, 1, 15)

    def test_us_slash(self):
        assert parse_date("01/28/2026") == date(2026, 1, 28)

    def test_month_name(self):
        assert parse_date("Jan 30 2026") == date(2026, 1, 30)
        assert parse_date("January 27, 2026") == date(2026, 1, 27)

    def test_ocr_artifact_year(self):
        # invoice 1012: "26-Jan-2O26" with letter O
        assert parse_date("26-Jan-2O26") == date(2026, 1, 26)

    def test_nonsense_and_missing(self):
        assert parse_date("yesterday") is None
        assert parse_date(None) is None
        assert parse_date("  ") is None


class TestCurrency:
    def test_eur_fixed_rate(self):
        assert to_usd(4125.00, "EUR") == (4537.50, 1.10)

    def test_unknown_currency(self):
        assert to_usd(100.0, "GBP") == (None, None)


def _invoice_1013_like() -> InvoiceData:
    return InvoiceData(
        invoice_number="INV-1013",
        vendor_name="Atlas Industrial Supply",
        line_items=[
            LineItem(item="WidgetA", quantity=15, unit_price=250.0, amount=3750.0),
            LineItem(item="WidgetB", quantity=10, unit_price=500.0, amount=5000.0),
            LineItem(item="GadgetX", quantity=5, unit_price=750.0, amount=3750.0),
            LineItem(item="WidgetA", quantity=5, unit_price=240.0, amount=1200.0),
            LineItem(item="WidgetB", quantity=8, unit_price=480.0, amount=3840.0),
            LineItem(item="GadgetX", quantity=3, unit_price=750.0, amount=2250.0),
            LineItem(item="WidgetA", quantity=2, unit_price=250.0, amount=500.0),
            LineItem(item="GadgetX", quantity=1, unit_price=750.0, amount=750.0),
        ],
        subtotal=21040.0,
        tax_rate=0.07,
        tax_amount=1472.80,
        total=22562.80,  # padded by exactly $50
    )


class TestMathCheck:
    def test_catches_1013_padded_grand_total(self):
        findings = check_math(_invoice_1013_like())
        codes = [f.code for f in findings]
        assert codes == ["total_mismatch"]
        assert "$+50.00" in findings[0].message.replace(",", "")

    def test_catches_1009_subtotal_and_negative_total(self):
        data = InvoiceData(
            line_items=[
                LineItem(item="WidgetA", quantity=-5, unit_price=250.0),
                LineItem(item="WidgetB", quantity=2, unit_price=500.0),
            ],
            subtotal=1000.0,
            tax_rate=0.0,
            tax_amount=0.0,
            total=-250.0,
        )
        codes = {f.code for f in check_math(data)}
        assert "subtotal_mismatch" in codes
        assert "negative_total" in codes

    def test_clean_invoice_has_no_findings(self):
        data = InvoiceData(
            line_items=[
                LineItem(item="WidgetA", quantity=10, unit_price=250.0),
                LineItem(item="WidgetB", quantity=5, unit_price=500.0),
            ],
            subtotal=5000.0,
            tax_rate=0.0,
            tax_amount=0.0,
            total=5000.0,
        )
        assert check_math(data) == []

    def test_shipping_included_in_total(self):
        # invoice 1010: total = subtotal + tax + shipping
        data = InvoiceData(
            line_items=[LineItem(item="WidgetA", quantity=8, unit_price=250.0, amount=2000.0)],
            subtotal=2000.0,
            tax_amount=100.0,
            shipping=150.0,
            total=2250.0,
        )
        assert check_math(data) == []


class TestFraudScoring:
    def test_1003_style_invoice_is_critical(self):
        data = InvoiceData(
            invoice_number="INV-1003",
            vendor_name="Fraudster LLC",
            invoice_date="2026-01-20",
            due_date="yesterday",
            line_items=[LineItem(item="FakeItem", quantity=100, unit_price=1000.0)],
            total=100000.0,
            payment_terms="Immediate",
        )
        from invoice_agent.models import StockCheck

        checks = [
            StockCheck(
                requested_item="FakeItem",
                matched_item="FakeItem",
                match_type="exact",
                requested_qty=100,
                available=0,
                sufficient=False,
            )
        ]
        score, signals, level = score_fraud(
            data,
            raw_text="URGENT - Pay immediately to avoid penalties!!! Wire transfer preferred.",
            stock_checks=checks,
            invoice_date=date(2026, 1, 20),
            due_date=None,
        )
        assert level == "critical"
        assert score >= 60

    def test_clean_invoice_is_low(self):
        data = InvoiceData(
            vendor_name="Widgets Inc.",
            invoice_date="2026-01-15",
            due_date="2026-02-01",
            line_items=[LineItem(item="WidgetA", quantity=10, unit_price=250.0)],
            total=2500.0,
        )
        score, signals, level = score_fraud(
            data, "INVOICE ...", [], date(2026, 1, 15), date(2026, 2, 1)
        )
        assert level == "low"
        assert signals == []


def test_parenthetical_qualifier_still_matches_inventory():
    # invoice 1010: "WidgetA (rush order)" is WidgetA at a premium, not a new SKU
    assert match_item("WidgetA (rush order)", INVENTORY) == ("WidgetA", "normalized")
