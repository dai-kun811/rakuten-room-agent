"""Authenticated, read-only ROOM detail evidence for promotional X releases."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from room_live_gateway import OWN_ROOM_URL
from room_poster import RoomPoster
from room_profile_lock import room_profile_lock


DETAIL_PATH = re.compile(r"^/tora_papa/\d+/?$")


class RoomEvidenceError(RuntimeError):
    """Exact authenticated ROOM evidence could not be established."""


def canonical_item_url(value: str) -> str:
    parsed = urlparse(unquote(value or ""))
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.scheme != "https" or parsed.netloc.lower() != "item.rakuten.co.jp" or len(parts) < 2:
        return ""
    return f"https://item.rakuten.co.jp/{parts[0].lower()}/{parts[1].lower()}"


def href_contains_product(href: str, product_url: str) -> bool:
    expected = canonical_item_url(product_url)
    if not expected:
        return False
    candidates = [href, unquote(href)]
    parsed = urlparse(href)
    for values in parse_qs(parsed.query).values():
        candidates.extend(values)
        candidates.extend(unquote(value) for value in values)
    return any(canonical_item_url(value) == expected for value in candidates)


def detail_urls_from_hrefs(hrefs: Iterable[str]) -> list[str]:
    found: list[str] = []
    for href in hrefs:
        absolute = urljoin("https://room.rakuten.co.jp", str(href or ""))
        parsed = urlparse(absolute)
        if parsed.netloc.lower() != "room.rakuten.co.jp" or not DETAIL_PATH.fullmatch(parsed.path):
            continue
        clean = f"https://room.rakuten.co.jp{parsed.path.rstrip('/')}"
        if clean not in found:
            found.append(clean)
    return found


def find_room_detail_for_product(
    product_url: str,
    *,
    profile_dir: Path,
    profile_lock_path: Path,
    headless: bool = True,
    max_details: int = 12,
) -> dict[str, str]:
    expected = canonical_item_url(product_url)
    if not expected:
        raise RoomEvidenceError("prepared product URL is invalid")
    from playwright.sync_api import sync_playwright

    with room_profile_lock(profile_lock_path, timeout_seconds=0):
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(Path(profile_dir).resolve()), channel="chrome", headless=headless
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(30_000)
                page.goto(OWN_ROOM_URL, wait_until="commit", timeout=60_000)
                page.wait_for_timeout(3_000)
                RoomPoster._assert_authenticated(page)
                hrefs = page.locator("a[href]").evaluate_all(
                    "elements => elements.map(element => element.getAttribute('href') || '')"
                )
                details = detail_urls_from_hrefs(hrefs)[:max_details]
                for detail_url in details:
                    page.goto(detail_url, wait_until="commit", timeout=60_000)
                    page.wait_for_timeout(1_500)
                    RoomPoster._assert_authenticated(page)
                    item_hrefs = page.locator("a[href]").evaluate_all(
                        "elements => elements.map(element => element.href || element.getAttribute('href') || '')"
                    )
                    if any(href_contains_product(href, expected) for href in item_hrefs):
                        return {"room_detail_url": detail_url, "product_url": expected}
            finally:
                context.close()
    raise RoomEvidenceError("matching ROOM detail was not found; X promotion remains stopped")
