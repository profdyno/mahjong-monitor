"""Decide whether a product page is showing the item as in-stock."""

from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup


def _json_ld_in_stock(soup: BeautifulSoup) -> bool | None:
    """Inspect Schema.org JSON-LD for offers.availability.

    Returns True/False if a definitive answer is found, None if there's
    nothing to go on (caller should fall back to the next check).
    """
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        for node in _iter_ld_nodes(data):
            offers = node.get("offers")
            if not offers:
                continue
            availability = _extract_availability(offers)
            if availability is None:
                continue
            return "instock" in availability.lower() or "presale" in availability.lower()
    return None


def _iter_ld_nodes(data):
    if isinstance(data, list):
        for item in data:
            yield from _iter_ld_nodes(item)
    elif isinstance(data, dict):
        yield data
        for value in data.values():
            if isinstance(value, (list, dict)):
                yield from _iter_ld_nodes(value)


def _extract_availability(offers) -> str | None:
    if isinstance(offers, list):
        for o in offers:
            a = _extract_availability(o)
            if a:
                return a
        return None
    if isinstance(offers, dict):
        return offers.get("availability")
    return None


def is_in_stock(html: str, checks: list[dict]) -> tuple[bool, str]:
    """Run the configured checks in order and return (in_stock, reason)."""
    soup = BeautifulSoup(html, "lxml")
    page_text = soup.get_text(" ", strip=True)

    for check in checks:
        kind = check.get("type")
        value = check.get("value", "")

        if kind == "json_ld":
            result = _json_ld_in_stock(soup)
            if result is not None:
                return result, f"json_ld availability -> {result}"

        elif kind == "text_absent":
            if not _contains_ci(page_text, value):
                return True, f"'{value}' absent from page"
            return False, f"'{value}' present on page"

        elif kind == "text_present":
            if _contains_ci(page_text, value):
                return True, f"'{value}' present on page"
            return False, f"'{value}' absent from page"

        elif kind == "css_absent":
            if not soup.select(value):
                return True, f"selector '{value}' matched nothing"
            return False, f"selector '{value}' matched"

        elif kind == "css_present":
            if soup.select(value):
                return True, f"selector '{value}' matched"
            return False, f"selector '{value}' matched nothing"

    # No check produced a verdict - be conservative, treat as out of stock.
    return False, "no check produced a verdict"


def _contains_ci(haystack: str, needle: str) -> bool:
    return re.search(re.escape(needle), haystack, re.IGNORECASE) is not None
