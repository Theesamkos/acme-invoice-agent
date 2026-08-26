"""Deterministic normalization helpers: item names, dates, currency.

All the messy-input handling lives here, LLM-free and unit-tested.
"""

from __future__ import annotations

import re
from datetime import date

from rapidfuzz import fuzz

# --- Item matching ----------------------------------------------------------

# Threshold rationale: SKU names here are short ("WidgetA"), where token-level
# similarity is treacherous -- fuzz.ratio("widgetc", "widgeta") ~= 86, but
# WidgetC is a genuinely unknown product and must NOT match WidgetA. 90+ keeps
# fuzzy matching for OCR-style noise while rejecting sibling SKUs.
FUZZY_THRESHOLD = 90.0


def normalize_item_name(name: str) -> str:
    """Lowercase and strip all non-alphanumerics: 'Widget A' -> 'widgeta'."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def match_item(requested: str, inventory_items: list[str]) -> tuple[str | None, str]:
    """Match an invoice item name against inventory. Returns (matched, match_type)."""
    by_normalized = {normalize_item_name(i): i for i in inventory_items}
    norm = normalize_item_name(requested)
    if requested in inventory_items:
        return requested, "exact"
    if norm in by_normalized:
        return by_normalized[norm], "normalized"
    best_score, best_item = 0.0, None
    for norm_name, original in by_normalized.items():
        score = fuzz.ratio(norm, norm_name)
        if score > best_score:
            best_score, best_item = score, original
    if best_item is not None and best_score >= FUZZY_THRESHOLD:
        return best_item, "fuzzy"
    return None, "unknown"


# --- Dates ------------------------------------------------------------------

_OCR_DIGIT_FIXES = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1"})

_DATE_PATTERNS = [
    "%Y-%m-%d",  # 2026-01-15
    "%m/%d/%Y",  # 01/28/2026
    "%d-%b-%Y",  # 26-Jan-2026
    "%b %d %Y",  # Jan 30 2026
    "%b %d, %Y",  # Jan 30, 2026
    "%B %d %Y",  # January 27 2026
    "%B %d, %Y",  # January 27, 2026
]


def _fix_ocr_digits(text: str) -> str:
    """Fix letter-for-digit OCR artifacts, but only inside digit-adjacent runs."""
    return re.sub(
        r"(?<=\d)[OolI]|[OolI](?=\d)",
        lambda m: m.group(0).translate(_OCR_DIGIT_FIXES),
        text,
    )


def parse_date(raw: str | None) -> date | None:
    """Parse a date string in any sample format; None if missing or unparseable."""
    if not raw or not raw.strip():
        return None
    cleaned = _fix_ocr_digits(raw.strip())
    cleaned = re.sub(r"\s+", " ", cleaned)
    from datetime import datetime

    for pattern in _DATE_PATTERNS:
        try:
            return datetime.strptime(cleaned, pattern).date()
        except ValueError:
            continue
    return None


# --- Currency ---------------------------------------------------------------

# Fixed demo rates (documented in the README): no live FX in a local pipeline.
EXCHANGE_RATES_TO_USD = {"USD": 1.0, "EUR": 1.10}


def to_usd(amount: float | None, currency: str) -> tuple[float | None, float | None]:
    """Convert an amount to USD. Returns (usd_amount, rate) -- (None, None) if unknown currency."""
    rate = EXCHANGE_RATES_TO_USD.get(currency.upper())
    if rate is None or amount is None:
        return None, rate
    return round(amount * rate, 2), rate
