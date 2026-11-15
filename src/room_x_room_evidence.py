"""Authenticated, read-only ROOM detail evidence for promotional X releases."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from room_live_gateway import OWN_ROOM_URL
from room_poster import RoomPoster
from room_profile_lock import room_profile_lock


DETAIL_PATH = re.compile(r"^/tora_papa/\d+/?$")
INITIAL_STATE_PATTERN = re.compile(
    r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});\s*window\.__REWIRED_SCHEMAS__",
    re.DOTALL,
)


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


def detail_urls_from_initial_state_scripts(
    scripts: Iterable[str], product_url: str
) -> list[str]:
    expected = canonical_item_url(product_url)
    if not expected:
        return []
    found: list[str] = []
    for script in scripts:
        match = INITIAL_STATE_PATTERN.search(str(script or ""))
        if match is None:
            continue
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        login = payload.get("loginStatus")
        user = payload.get("userData")
        if (
            not isinstance(login, dict)
            or login.get("is_login") is not True
            or not isinstance(user, dict)
            or user.get("username") != "tora_papa"
        ):
            continue
        recent = user.get("recent_collects")
        if not isinstance(recent, list):
            continue
        for collect in recent:
            if not isinstance(collect, dict):
                continue
            item = collect.get("item")
            collect_id = str(collect.get("id", ""))
            if (
                isinstance(item, dict)
                and canonical_item_url(str(item.get("url", ""))) == expected
                and collect_id.isdigit()
            ):
                detail_url = f"https://room.rakuten.co.jp/tora_papa/{collect_id}"
                if detail_url not in found:
                    found.append(detail_url)
    return found


def prioritized_card_indexes(
    product_url: str,
    image_sources: Iterable[str],
    *,
    max_details: int,
) -> list[int]:
    """Prefer likely matching ROOM cards, while retaining exact detail verification."""
    expected = canonical_item_url(product_url)
    if not expected:
        return []
    parts = [part for part in urlparse(expected).path.split("/") if part]
    shop, item = parts[:2]
    ranked: list[tuple[int, int]] = []
    for index, source in enumerate(image_sources):
        value = str(source or "").lower()
        if not value:
            continue
        score = 0
        if f"shop.r10s.jp/{shop}/" in value:
            score += 1
        if item in value:
            score += 2
        ranked.append((-score, index))
    ranked.sort()
    return [index for _, index in ranked[:max_details]]


def find_room_detail_for_product(
    product_url: str,
    *,
    profile_dir: Path,
    profile_lock_path: Path,
    headless: bool = True,
    max_details: int = 40,
) -> dict[str, str]:
    expected = canonical_item_url(product_url)
    if not expected:
        raise RoomEvidenceError("prepared product URL is invalid")
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
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
                details: list[str] = []
                for _ in range(10):
                    hrefs = page.locator("a[href]").evaluate_all(
                        "elements => elements.map(element => element.getAttribute('href') || '')"
                    )
                    scripts = page.locator("script").evaluate_all(
                        "elements => elements.map(element => element.textContent || '')"
                    )
                    details = detail_urls_from_initial_state_scripts(scripts, expected)
                    details.extend(
                        value
                        for value in detail_urls_from_hrefs(hrefs)
                        if value not in details
                    )
                    if details:
                        break
                    page.wait_for_timeout(1_000)
                details = details[:max_details]
                for detail_url in details:
                    page.goto(detail_url, wait_until="commit", timeout=60_000)
                    page.wait_for_timeout(1_500)
                    RoomPoster._assert_authenticated(page)
                    for _ in range(10):
                        item_hrefs = page.locator("a[href]").evaluate_all(
                            "elements => elements.map(element => element.href || element.getAttribute('href') || '')"
                        )
                        if any(href_contains_product(href, expected) for href in item_hrefs):
                            return {"room_detail_url": detail_url, "product_url": expected}
                        page.wait_for_timeout(1_000)
                page.goto(OWN_ROOM_URL, wait_until="commit", timeout=60_000)
                page.wait_for_timeout(5_000)
                RoomPoster._assert_authenticated(page)
                card_selector = "a[role='link']:not([href])"
                cards = page.locator(card_selector)
                image_sources = cards.evaluate_all(
                    "elements => elements.map(element => element.querySelector('img')?.src || '')"
                )
                card_indexes = prioritized_card_indexes(
                    expected, image_sources, max_details=max_details
                )
                for card_index in card_indexes:
                    if page.url != OWN_ROOM_URL:
                        page.goto(OWN_ROOM_URL, wait_until="commit", timeout=60_000)
                        page.wait_for_timeout(3_000)
                        RoomPoster._assert_authenticated(page)
                    cards = page.locator(card_selector)
                    if card_index >= cards.count():
                        continue
                    try:
                        cards.nth(card_index).click(timeout=10_000)
                        page.wait_for_url(
                            re.compile(r"https://room\.rakuten\.co\.jp/tora_papa/\d+/?$"),
                            wait_until="commit",
                            timeout=10_000,
                        )
                    except PlaywrightTimeoutError:
                        continue
                    page.wait_for_timeout(1_500)
                    RoomPoster._assert_authenticated(page)
                    parsed = urlparse(page.url)
                    if not DETAIL_PATH.fullmatch(parsed.path):
                        continue
                    item_hrefs = page.locator("a[href]").evaluate_all(
                        "elements => elements.map(element => element.href || element.getAttribute('href') || '')"
                    )
                    if any(href_contains_product(href, expected) for href in item_hrefs):
                        detail_url = f"https://room.rakuten.co.jp{parsed.path.rstrip('/')}"
                        return {"room_detail_url": detail_url, "product_url": expected}
            finally:
                context.close()
    raise RoomEvidenceError("matching ROOM detail was not found; X promotion remains stopped")
