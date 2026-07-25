from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import sys
import requests
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urljoin, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = PROJECT_ROOT / ".local" / "room-worker"
PROFILE_DIR = STATE_DIR / "chrome-profile"
LEDGER_PATH = STATE_DIR / "engagement-ledger.jsonl"
LOG_PATH = STATE_DIR / "engagement-worker.log"
SUMMARY_PATH = STATE_DIR / "daily-routine-summary.json"
ROUTINE_SOURCE = PROJECT_ROOT / "room-routine" / "index.html"
ROUTINE_URL = "https://dai-kun811.github.io/rakuten-room-agent/"
DAILY_GOAL = 50
RESET_HOUR = 5
OWN_ROOM_ID = "tora_papa"
CAPTCHA_PATTERN = re.compile(r"captcha|ロボットではありません|画像認証", re.IGNORECASE)
API_SEARCH_TIMEOUT_SECONDS = 20
MAX_API_SEARCH_PAGES = 2
BROWSER_SEARCH_TIMEOUT_MS = 8_000
MAX_DISCOVERY_SEARCH_URLS = 20
SUPPLEMENTAL_SEARCH_URLS = [
    "https://room.rakuten.co.jp/search/user?keyword=%E5%87%BA%E7%94%A3%E6%BA%96%E5%82%99&rank=6%2C5%2C4%2C3",
    "https://room.rakuten.co.jp/search/user?keyword=%E3%83%99%E3%83%93%E3%83%BC%E7%94%A8%E5%93%81&rank=6%2C5%2C4%2C3",
    "https://room.rakuten.co.jp/search/user?keyword=%E5%AD%90%E8%82%B2%E3%81%A6&rank=6%2C5%2C4%2C3",
    "https://room.rakuten.co.jp/search/user?keyword=%E3%83%99%E3%83%93%E3%83%BC%E3%82%B0%E3%83%83%E3%82%BA&rank=6%2C5%2C4%2C3",
    "https://room.rakuten.co.jp/search/user?keyword=%E3%83%9E%E3%83%9E%E3%82%B0%E3%83%83%E3%82%BA&rank=6%2C5%2C4%2C3",
    "https://room.rakuten.co.jp/search/user?keyword=%E7%B5%B5%E6%9C%AC&rank=6%2C5%2C4%2C3",
    "https://room.rakuten.co.jp/search/user?keyword=%E3%82%AD%E3%83%83%E3%82%BA%E7%94%A8%E5%93%81&rank=6%2C5%2C4%2C3",
    "https://room.rakuten.co.jp/search/user?keyword=%E9%80%9A%E5%9C%92%E3%82%B0%E3%83%83%E3%82%BA&rank=6%2C5%2C4%2C3",
    "https://room.rakuten.co.jp/search/user?keyword=%E9%9B%A2%E4%B9%B3%E9%A3%9F&rank=2%2C1",
    "https://room.rakuten.co.jp/search/user?keyword=%E5%AD%90%E3%81%A9%E3%82%82%E3%81%A8%E6%9A%AE%E3%82%89%E3%81%99&rank=2%2C1",
    "https://room.rakuten.co.jp/search/user?keyword=%E7%9F%A5%E8%82%B2%E7%8E%A9%E5%85%B7&rank=2%2C1",
    "https://room.rakuten.co.jp/search/user?keyword=%E8%82%B2%E5%85%90%E4%BE%BF%E5%88%A9%E3%82%B0%E3%83%83%E3%82%BA&rank=2%2C1",
    "https://room.rakuten.co.jp/search/user?keyword=%E5%87%BA%E7%94%A3%E6%BA%96%E5%82%99&rank=2%2C1",
    "https://room.rakuten.co.jp/search/user?keyword=%E3%83%99%E3%83%93%E3%83%BC%E7%94%A8%E5%93%81&rank=2%2C1",
    "https://room.rakuten.co.jp/search/user?keyword=%E5%AD%90%E8%82%B2%E3%81%A6&rank=2%2C1",
    "https://room.rakuten.co.jp/search/user?keyword=%E3%83%99%E3%83%93%E3%83%BC%E3%82%B0%E3%83%83%E3%82%BA&rank=2%2C1",
    "https://room.rakuten.co.jp/search/user?keyword=%E7%B5%B5%E6%9C%AC&rank=2%2C1",
    "https://room.rakuten.co.jp/search/user?keyword=%E3%82%AD%E3%83%83%E3%82%BA%E7%94%A8%E5%93%81&rank=2%2C1",
    "https://room.rakuten.co.jp/search/user?keyword=%E9%9B%A2%E4%B9%B3%E9%A3%9F",
    "https://room.rakuten.co.jp/search/user?keyword=%E5%AD%90%E8%82%B2%E3%81%A6",
    "https://room.rakuten.co.jp/search/user?keyword=%E7%9F%A5%E8%82%B2%E7%8E%A9%E5%85%B7",
    "https://room.rakuten.co.jp/search/user?keyword=%E8%82%B2%E5%85%90%E4%BE%BF%E5%88%A9%E3%82%B0%E3%83%83%E3%82%BA",
    "https://room.rakuten.co.jp/search/user?keyword=%E5%87%BA%E7%94%A3%E6%BA%96%E5%82%99",
    "https://room.rakuten.co.jp/search/user?keyword=%E3%83%99%E3%83%93%E3%83%BC%E7%94%A8%E5%93%81",
    "https://room.rakuten.co.jp/search/user?keyword=%E7%B5%B5%E6%9C%AC",
    "https://room.rakuten.co.jp/search/user?keyword=%E3%82%AD%E3%83%83%E3%82%BA%E7%94%A8%E5%93%81",
]


class EngagementError(RuntimeError):
    pass


@dataclass(frozen=True)
class RoutineCandidate:
    id: str
    name: str
    url: str
    tags: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class EngagementResult:
    candidate_id: str
    followed: bool
    liked: bool
    follow_status: str
    like_status: str


def extract_json_constant(source: str, name: str) -> Any:
    match = re.search(
        rf"const\s+{re.escape(name)}\s*=\s*(\[.*?\]);",
        source,
        flags=re.DOTALL,
    )
    if not match:
        raise ValueError(f"{name} was not found in the routine page.")
    return json.loads(match.group(1))


def load_routine_sources(path: Path = ROUTINE_SOURCE) -> tuple[list[RoutineCandidate], list[str]]:
    source = path.read_text(encoding="utf-8")
    raw_candidates = extract_json_constant(source, "DEFAULT_CANDIDATES")
    search_block = re.search(
        r"const\s+SEARCH_LINKS\s*=\s*(\[.*?\]);",
        source,
        flags=re.DOTALL,
    )
    if not search_block:
        raise ValueError("SEARCH_LINKS was not found in the routine page.")
    candidates = [
        RoutineCandidate(
            id=str(item["id"]),
            name=str(item.get("name", item["id"])),
            url=str(item["url"]),
            tags=tuple(str(tag) for tag in item.get("tags", [])),
            note=str(item.get("note", "")),
        )
        for item in raw_candidates
        if item.get("id") and item.get("url")
    ]
    search_urls = re.findall(r'url:\s*"(https://room\.rakuten\.co\.jp/search/user[^"]+)"', search_block.group(1))
    search_urls = list(dict.fromkeys([*search_urls, *SUPPLEMENTAL_SEARCH_URLS]))
    return candidates, search_urls


def routine_date(now: datetime | None = None, reset_hour: int = RESET_HOUR) -> str:
    local_now = now or datetime.now().astimezone()
    if local_now.hour < reset_hour:
        local_now -= timedelta(days=1)
    return local_now.date().isoformat()


def read_ledger(path: Path = LEDGER_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def append_ledger_event(event: dict[str, Any], path: Path = LEDGER_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def progress_by_candidate(
    events: Iterable[dict[str, Any]],
    *,
    day: str | None = None,
) -> dict[str, dict[str, bool]]:
    progress: dict[str, dict[str, bool]] = {}
    for event in events:
        if day is not None and event.get("routine_date") != day:
            continue
        candidate_id = str(event.get("candidate_id", "")).strip()
        if not candidate_id:
            continue
        current = progress.setdefault(candidate_id, {"followed": False, "liked": False})
        current["followed"] = current["followed"] or bool(event.get("followed"))
        current["liked"] = current["liked"] or bool(event.get("liked"))
    return progress


def progress_totals(progress: dict[str, dict[str, bool]]) -> tuple[int, int]:
    return (
        sum(1 for item in progress.values() if item["followed"]),
        sum(1 for item in progress.values() if item["liked"]),
    )


def build_daily_candidate_queue(
    source_candidates: Iterable[RoutineCandidate],
    today_progress: dict[str, dict[str, bool]],
) -> list[RoutineCandidate]:
    candidates = list(source_candidates)
    known = {candidate.id: candidate for candidate in candidates}
    partial_ids = [
        candidate_id
        for candidate_id, item in today_progress.items()
        if not (item["followed"] and item["liked"])
    ]
    queue = [known[candidate_id] for candidate_id in partial_ids if candidate_id in known]
    queue.extend(
        candidate
        for candidate in candidates
        if candidate.id not in partial_ids
        and not (
            today_progress.get(candidate.id, {}).get("followed")
            and today_progress.get(candidate.id, {}).get("liked")
        )
    )
    return queue


def candidate_from_room_url(url: str, name: str = "") -> RoutineCandidate | None:
    parsed = urlparse(url)
    if parsed.netloc.lower() != "room.rakuten.co.jp":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2 or parts[1] != "items" or parts[0] == OWN_ROOM_ID:
        return None
    candidate_id = parts[0]
    return RoutineCandidate(
        id=candidate_id,
        name=name.strip() or candidate_id,
        url=f"https://room.rakuten.co.jp/{candidate_id}/items",
        tags=("自動更新候補",),
        note="楽天ROOM公開検索から自動更新",
    )


def candidate_from_search_user(user: Any) -> RoutineCandidate | None:
    if not isinstance(user, dict):
        return None
    username = str(user.get("username", "")).strip()
    fullname = str(user.get("fullname", "")).strip()
    if not username:
        return None
    return candidate_from_room_url(
        f"https://room.rakuten.co.jp/{username}/items",
        fullname,
    )


def discover_candidates_from_api(
    search_url: str,
    *,
    http_get: Any = requests.get,
) -> list[RoutineCandidate]:
    query = parse_qs(urlparse(search_url).query)
    base_params: dict[str, str | int] = {
        "query": query.get("keyword", [""])[0],
    }
    for source, target in (("follower", "followers"), ("items", "collects"), ("rank", "rank")):
        value = query.get(source, [""])[0]
        if value:
            base_params[target] = value

    found: dict[str, RoutineCandidate] = {}
    for page in range(1, MAX_API_SEARCH_PAGES + 1):
        response = http_get(
            "https://room.rakuten.co.jp/api/user/search",
            params={**base_params, "page": page},
            headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": search_url,
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
                ),
            },
            timeout=API_SEARCH_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("status") not in {"success", "next page"}:
            break
        users = payload.get("data", [])
        if not isinstance(users, list) or not users:
            break
        for user in users:
            candidate = candidate_from_search_user(user)
            if candidate is not None:
                found[candidate.id] = candidate
        if len(users) < 20:
            break
    return list(found.values())


class RoomEngagementDriver:
    def __init__(self, *, timeout_ms: int = 30_000) -> None:
        self.timeout_ms = timeout_ms

    def engage(
        self,
        page: Any,
        candidate: RoutineCandidate,
        *,
        need_follow: bool,
        need_like: bool,
    ) -> EngagementResult:
        page.set_default_timeout(self.timeout_ms)
        if hasattr(page, "set_default_navigation_timeout"):
            page.set_default_navigation_timeout(self.timeout_ms)
        page.goto(candidate.url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        self._assert_safe_page(page)
        followed, follow_status = self._follow(page) if need_follow else (False, "goal_reached")
        liked, like_status = self._like(page) if need_like else (False, "goal_reached")
        return EngagementResult(
            candidate_id=candidate.id,
            followed=followed,
            liked=liked,
            follow_status=follow_status,
            like_status=like_status,
        )

    @staticmethod
    def _assert_safe_page(page: Any) -> None:
        lowered_url = page.url.lower()
        if "login" in lowered_url or "signin" in lowered_url:
            raise EngagementError("楽天ROOMのログイン状態が期限切れです。")
        password = page.locator('input[type="password"]').first
        try:
            if password.is_visible():
                raise EngagementError("楽天ROOMのログイン状態が期限切れです。")
        except EngagementError:
            raise
        except Exception:
            pass
        body_text = page.locator("body").inner_text(timeout=5_000)
        if CAPTCHA_PATTERN.search(body_text):
            raise EngagementError("楽天ROOMで画像認証が表示されたため停止しました。")

    @staticmethod
    def _action_elements(page: Any) -> list[Any]:
        locator = page.locator('button, [role="button"]')
        return [locator.nth(index) for index in range(min(locator.count(), 120))]

    def _follow(self, page: Any) -> tuple[bool, str]:
        follow_button = None
        for element in self._action_elements(page):
            try:
                if not element.is_visible():
                    continue
                text = " ".join(element.inner_text().split())
                label = " ".join((element.get_attribute("aria-label") or "").split())
            except Exception:
                continue
            combined = label or text
            if combined in {"フォロー中", "フォロー済み"}:
                return True, "already_following"
            if combined in {"フォロー", "フォローする"}:
                follow_button = element
                break
        if follow_button is None:
            return False, "follow_button_not_found"

        before = self._element_state(follow_button)
        follow_button.click()
        page.wait_for_timeout(800)
        after = self._element_state(follow_button)
        if after != before or "フォロー中" in " ".join(after):
            return True, "followed"
        return False, "follow_unverified"

    def _like(self, page: Any) -> tuple[bool, str]:
        like_buttons = page.locator('button:has([class*="rex-favorite-"])')
        try:
            like_buttons.first.wait_for(state="visible", timeout=self.timeout_ms)
        except Exception:
            return False, "like_button_not_found"

        for index in range(min(like_buttons.count(), 120)):
            element = like_buttons.nth(index)
            try:
                if not element.is_visible():
                    continue
                icon_class = self._favorite_icon_class(element)
            except Exception:
                continue
            if "favorite-filled" in icon_class:
                continue
            before = self._element_state(element)
            element.click()
            page.wait_for_timeout(800)
            after = self._element_state(element)
            if "favorite-filled" in self._favorite_icon_class(element):
                return True, "liked"
            if after != before and "favorite-outline" not in after[3]:
                return True, "liked"
            return False, "like_unverified"
        return False, "like_button_not_found"

    @staticmethod
    def _favorite_icon_class(element: Any) -> str:
        try:
            icon = element.locator('[class*="rex-favorite-"]').first
            return (icon.get_attribute("class") or "").lower()
        except Exception:
            return ""

    @staticmethod
    def _element_state(element: Any) -> tuple[str, str, str, str]:
        try:
            root_class = (element.get_attribute("class") or "").lower()
            favorite_class = RoomEngagementDriver._favorite_icon_class(element)
            return (
                " ".join(element.inner_text().split()),
                " ".join((element.get_attribute("aria-label") or "").split()),
                (element.get_attribute("aria-pressed") or "").lower(),
                " ".join(part for part in (root_class, favorite_class) if part),
            )
        except Exception:
            return ("detached", "", "", "")


def discover_candidates(
    page: Any,
    search_urls: Iterable[str],
    *,
    exclude_ids: Iterable[str] = (),
) -> list[RoutineCandidate]:
    logger = logging.getLogger("room-engagement-worker")
    found: dict[str, RoutineCandidate] = {}
    excluded = set(exclude_ids)
    page.set_default_timeout(BROWSER_SEARCH_TIMEOUT_MS)
    if hasattr(page, "set_default_navigation_timeout"):
        page.set_default_navigation_timeout(BROWSER_SEARCH_TIMEOUT_MS)
    for index, search_url in enumerate(search_urls):
        if index >= MAX_DISCOVERY_SEARCH_URLS:
            break
        try:
            api_candidates = discover_candidates_from_api(search_url)
        except (requests.RequestException, ValueError, TypeError):
            api_candidates = []
        fresh_api_candidates = [candidate for candidate in api_candidates if candidate.id not in excluded]
        for candidate in fresh_api_candidates:
            found[candidate.id] = candidate
        if fresh_api_candidates:
            continue

        try:
            page.goto(
                search_url,
                wait_until="domcontentloaded",
                timeout=BROWSER_SEARCH_TIMEOUT_MS,
            )
            submit_user_search(page)
            for _ in range(5):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(700)
            links = page.locator('a[href*="/items"]')
            for link_index in range(min(links.count(), 300)):
                link = links.nth(link_index)
                try:
                    href = urljoin(page.url, link.get_attribute("href") or "")
                    name = " ".join(link.inner_text().split())
                except Exception:
                    continue
                candidate = candidate_from_room_url(href, name)
                if candidate and candidate.id not in excluded:
                    found[candidate.id] = candidate
            model_links = page.locator('a[ng-click="goToUserRoom()"]')
            for link_index in range(min(model_links.count(), 300)):
                link = model_links.nth(link_index)
                try:
                    user = link.evaluate(
                        """element => {
                            const angular = window.angular;
                            if (!angular) return null;
                            const scope = angular.element(element).scope();
                            if (!scope || !scope.user) return null;
                            return {
                                username: scope.user.username,
                                fullname: scope.user.fullname,
                            };
                        }"""
                    )
                except Exception:
                    continue
                candidate = candidate_from_search_user(user)
                if candidate and candidate.id not in excluded:
                    found[candidate.id] = candidate
        except Exception as exc:
            logger.warning("Candidate browser discovery skipped index=%s error=%s", index, type(exc).__name__)
    return list(found.values())


def submit_user_search(page: Any) -> bool:
    try:
        search_input = page.locator('input[name="search_keyword"]').first
        if not search_input.is_visible():
            return False
        search_input.press("Enter")
        page.wait_for_load_state("domcontentloaded", timeout=BROWSER_SEARCH_TIMEOUT_MS)
        page.wait_for_timeout(3_000)
        return True
    except Exception:
        return False


def sync_routine_page(
    page: Any,
    *,
    day: str,
    candidates: Iterable[RoutineCandidate],
    progress: dict[str, dict[str, bool]],
) -> None:
    payload = {
        "day": day,
        "candidates": [asdict(candidate) for candidate in candidates],
        "progress": progress,
        "storageKey": "rakutenRoomRoutine.v1",
    }
    page.goto(ROUTINE_URL, wait_until="domcontentloaded")
    page.evaluate(
        """
        payload => {
          let state;
          try {
            state = JSON.parse(localStorage.getItem(payload.storageKey) || "{}");
          } catch (_) {
            state = {};
          }
          state.candidates = Array.isArray(state.candidates) ? state.candidates : [];
          state.progress = state.progress && typeof state.progress === "object" ? state.progress : {};
          state.daily = state.daily && typeof state.daily === "object" ? state.daily : {};
          const existing = new Map(state.candidates.map(item => [item.id, item]));
          for (const item of payload.candidates) {
            existing.set(item.id, {...existing.get(item.id), ...item, source: "automated-routine"});
          }
          state.candidates = [...existing.values()];
          state.daily[payload.day] = payload.candidates.map(item => item.id);
          const now = new Date().toISOString();
          for (const [id, item] of Object.entries(payload.progress)) {
            state.progress[id] = state.progress[id] || {};
            if (item.followed) state.progress[id].followedAt = state.progress[id].followedAt || now;
            if (item.liked) state.progress[id].likedAt = state.progress[id].likedAt || now;
            if (item.followed || item.liked) state.progress[id].skippedAt = "";
          }
          state.createdAt = state.createdAt || now;
          localStorage.setItem(payload.storageKey, JSON.stringify(state));
        }
        """,
        payload,
    )


def configure_logging() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
    )


def write_and_publish_summary(summary: dict[str, Any], logger: logging.Logger) -> bool:
    SUMMARY_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        from room_progress_publisher import publish_progress

        publish_progress(summary)
        summary["published"] = True
        logger.info(
            "Public routine progress updated day=%s follow=%s like=%s completed=%s",
            summary.get("routine_date"),
            summary.get("followed"),
            summary.get("liked"),
            summary.get("completed"),
        )
    except Exception as exc:
        summary["published"] = False
        summary["publish_error"] = type(exc).__name__
        logger.error("Public routine progress update failed error=%s", type(exc).__name__)
    SUMMARY_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return bool(summary["published"])


def run(*, apply: bool, goal: int, min_delay: float, max_delay: float, headless: bool) -> int:
    configure_logging()
    logger = logging.getLogger("room-engagement-worker")
    day = routine_date()
    source_candidates, search_urls = load_routine_sources()
    events = read_ledger()
    today_progress = progress_by_candidate(events, day=day)
    followed, liked = progress_totals(today_progress)
    if followed >= goal and liked >= goal:
        logger.info("Daily engagement goal already complete follow=%s like=%s", followed, liked)
        if not apply:
            return 0
        summary = {
            "routine_date": day,
            "followed": followed,
            "liked": liked,
            "goal": goal,
            "attempted": 0,
            "failures": 0,
            "completed": True,
        }
        return 0 if write_and_publish_summary(summary, logger) else 1
    if not apply:
        logger.info(
            "Dry run day=%s candidates=%s follow=%s/%s like=%s/%s",
            day,
            len(source_candidates),
            followed,
            goal,
            liked,
            goal,
        )
        return 0
    if not PROFILE_DIR.exists():
        logger.error("ROOM browser profile is missing.")
        return 1

    from playwright.sync_api import sync_playwright

    attempted: set[str] = set()
    known = {candidate.id: candidate for candidate in source_candidates}
    queue = build_daily_candidate_queue(source_candidates, today_progress)
    driver = RoomEngagementDriver()
    failures = 0
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            channel="chrome",
            headless=headless,
        )
        work_page = context.new_page()
        routine_page = context.new_page()
        try:
            initial_batch = list(today_progress)
            initial_batch.extend(
                candidate.id for candidate in queue if candidate.id not in initial_batch
            )
            batch_candidates = [known[candidate_id] for candidate_id in initial_batch[:goal] if candidate_id in known]
            try:
                sync_routine_page(
                    routine_page,
                    day=day,
                    candidates=batch_candidates,
                    progress=today_progress,
                )
            except Exception as exc:
                logger.warning("Routine page initial sync failed error=%s", type(exc).__name__)

            while followed < goal or liked < goal:
                candidate = next((item for item in queue if item.id not in attempted), None)
                if candidate is None:
                    completed_or_attempted_ids = {
                        candidate_id
                        for candidate_id, item in today_progress.items()
                        if item.get("followed") and item.get("liked")
                    }
                    completed_or_attempted_ids.update(attempted)
                    discovered = discover_candidates(
                        work_page,
                        search_urls,
                        exclude_ids=completed_or_attempted_ids,
                    )
                    for item in discovered:
                        known[item.id] = item
                        if item.id not in attempted and not (
                            today_progress.get(item.id, {}).get("followed")
                            and today_progress.get(item.id, {}).get("liked")
                        ):
                            queue.append(item)
                    candidate = next((item for item in queue if item.id not in attempted), None)
                if candidate is None:
                    logger.error("Candidate pool was exhausted before reaching the daily goal.")
                    break

                attempted.add(candidate.id)
                current = today_progress.get(candidate.id, {"followed": False, "liked": False})
                need_follow = followed < goal and not current["followed"]
                need_like = liked < goal and not current["liked"]
                try:
                    result = driver.engage(
                        work_page,
                        candidate,
                        need_follow=need_follow,
                        need_like=need_like,
                    )
                    event = {
                        "timestamp": datetime.now().astimezone().isoformat(),
                        "routine_date": day,
                        "candidate_id": candidate.id,
                        "room_url": candidate.url,
                        "followed": current["followed"] or result.followed,
                        "liked": current["liked"] or result.liked,
                        "follow_status": result.follow_status,
                        "like_status": result.like_status,
                    }
                    append_ledger_event(event)
                    today_progress = progress_by_candidate([*events, event], day=day)
                    events.append(event)
                    followed, liked = progress_totals(today_progress)
                    logger.info(
                        "Engagement progress candidate=%s follow=%s/%s like=%s/%s",
                        candidate.id,
                        followed,
                        goal,
                        liked,
                        goal,
                    )
                except EngagementError as exc:
                    logger.error("Engagement stopped candidate=%s error=%s", candidate.id, str(exc))
                    failures += 1
                    if "画像認証" in str(exc) or "ログイン状態" in str(exc):
                        break
                except Exception as exc:
                    logger.error(
                        "Engagement failed candidate=%s error=%s",
                        candidate.id,
                        type(exc).__name__,
                    )
                    failures += 1

                if followed < goal or liked < goal:
                    delay = random.uniform(min_delay, max_delay)
                    work_page.wait_for_timeout(int(delay * 1_000))

            final_ids = list(today_progress)
            final_candidates = [known[candidate_id] for candidate_id in final_ids if candidate_id in known]
            try:
                sync_routine_page(
                    routine_page,
                    day=day,
                    candidates=final_candidates,
                    progress=today_progress,
                )
                routine_page.reload(wait_until="domcontentloaded")
            except Exception as exc:
                logger.warning("Routine page final sync failed error=%s", type(exc).__name__)
        finally:
            context.close()

    summary = {
        "routine_date": day,
        "followed": followed,
        "liked": liked,
        "goal": goal,
        "attempted": len(attempted),
        "failures": failures,
        "completed": followed >= goal and liked >= goal,
    }
    published = write_and_publish_summary(summary, logger)
    return 0 if summary["completed"] and published else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Automate the daily Rakuten ROOM follow/like routine.")
    parser.add_argument("--apply", action="store_true", help="Perform follow and like actions.")
    parser.add_argument("--goal", type=int, default=DAILY_GOAL)
    parser.add_argument("--min-delay", type=float, default=12.0)
    parser.add_argument("--max-delay", type=float, default=25.0)
    parser.add_argument("--headful", action="store_true")
    args = parser.parse_args()
    if not (1 <= args.goal <= DAILY_GOAL):
        parser.error(f"--goal must be between 1 and {DAILY_GOAL}")
    if args.min_delay < 0 or args.max_delay < args.min_delay:
        parser.error("Invalid delay range")
    return run(
        apply=args.apply,
        goal=args.goal,
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        headless=not args.headful,
    )


if __name__ == "__main__":
    sys.exit(main())
