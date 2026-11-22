from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import room_engagement_worker as engagement_worker
from room_engagement_worker import (
    RoomEngagementDriver,
    RoutineCandidate,
    build_daily_candidate_queue,
    candidate_from_room_url,
    candidate_from_search_user,
    candidates_from_events,
    discover_candidates,
    discover_candidates_from_api,
    extract_json_constant,
    followed_candidate_ids,
    load_routine_sources,
    next_candidate_for_goals,
    progress_by_candidate,
    progress_totals,
    routine_date,
    submit_user_search,
)
from room_engagement_state import finish, record_action, start_gate
from room_operation_contract import JST


class FakeFavoriteIcon:
    def __init__(self, button) -> None:
        self.button = button

    @property
    def first(self):
        return self

    def get_attribute(self, name: str):
        return self.button.icon_class if name == "class" else None


class FakeFavoriteButton:
    def __init__(self, *, filled: bool = False) -> None:
        self.icon_class = "rex-favorite-filled--test" if filled else "rex-favorite-outline--test"
        self.click_count = 0

    def locator(self, _selector: str):
        return FakeFavoriteIcon(self)

    def is_visible(self) -> bool:
        return True

    def wait_for(self, **_kwargs) -> None:
        pass

    def inner_text(self) -> str:
        return ""

    def get_attribute(self, name: str):
        return "image-icon--test" if name == "class" else None

    def click(self) -> None:
        self.click_count += 1
        self.icon_class = "rex-favorite-filled--test"


class FakeFavoriteButtons:
    def __init__(self, buttons) -> None:
        self.buttons = buttons

    @property
    def first(self):
        return self.buttons[0]

    def count(self) -> int:
        return len(self.buttons)

    def nth(self, index: int):
        return self.buttons[index]


class FakePage:
    def __init__(self, buttons) -> None:
        self.buttons = FakeFavoriteButtons(buttons)

    def locator(self, _selector: str):
        return self.buttons

    def wait_for_timeout(self, _milliseconds: int) -> None:
        pass


class FakeSearchInput:
    def __init__(self) -> None:
        self.pressed = ""

    @property
    def first(self):
        return self

    def is_visible(self) -> bool:
        return True

    def press(self, key: str) -> None:
        self.pressed = key


class FakeSearchPage:
    def __init__(self) -> None:
        self.search_input = FakeSearchInput()
        self.waited_for = ""

    def locator(self, _selector: str):
        return self.search_input

    def wait_for_load_state(self, state: str, **_kwargs) -> None:
        self.waited_for = state

    def wait_for_timeout(self, _milliseconds: int) -> None:
        pass


class RoomEngagementWorkerTest(unittest.TestCase):
    def test_discovers_candidates_from_public_user_search_api(self) -> None:
        class FakeResponse:
            def __init__(self, payload) -> None:
                self.payload = payload

            def raise_for_status(self) -> None:
                pass

            def json(self):
                return self.payload

        calls = []

        def fake_get(url, *, params, headers, timeout):
            calls.append((url, params, headers, timeout))
            if params["page"] == 1:
                return FakeResponse(
                    {
                        "status": "success",
                        "data": [
                            {"username": "room_example", "fullname": "Example User"},
                            {"fullname": "Missing username"},
                            *({"username": f"room_page1_{index}"} for index in range(18)),
                        ],
                    }
                )
            if params["page"] == 2:
                return FakeResponse(
                    {
                        "status": "success",
                        "data": [
                            {"username": "room_example", "fullname": "Example User"},
                            {"username": "room_page2", "fullname": "Page Two"},
                        ],
                    }
                )
            return FakeResponse({"status": "not found", "data": []})

        candidates = discover_candidates_from_api(
            "https://room.rakuten.co.jp/search/user?keyword=%E9%9B%A2%E4%B9%B3%E9%A3%9F&rank=6%2C5",
            http_get=fake_get,
        )

        self.assertEqual(candidates[0].id, "room_example")
        self.assertEqual(candidates[-1].id, "room_page2")
        self.assertEqual(len(candidates), 20)
        self.assertEqual(calls[0][0], "https://room.rakuten.co.jp/api/user/search")
        self.assertEqual(calls[0][1], {"query": "離乳食", "page": 1, "rank": "6,5"})
        self.assertEqual(calls[1][1], {"query": "離乳食", "page": 2, "rank": "6,5"})
        self.assertEqual(len(calls), 2)
        self.assertIn("Mozilla/5.0", calls[0][2]["User-Agent"])
        self.assertEqual(calls[0][3], 20)

    def test_user_search_submits_prefilled_keyword(self) -> None:
        page = FakeSearchPage()

        submitted = submit_user_search(page)

        self.assertTrue(submitted)
        self.assertEqual(page.search_input.pressed, "Enter")
        self.assertEqual(page.waited_for, "domcontentloaded")

    def test_like_never_skips_latest_post_to_like_an_older_post(self) -> None:
        filled = FakeFavoriteButton(filled=True)
        unfilled = FakeFavoriteButton()
        page = FakePage([filled, unfilled])

        liked, status = RoomEngagementDriver()._like(page)

        self.assertTrue(liked)
        self.assertEqual(status, "already_liked_paired_post")
        self.assertEqual(filled.click_count, 0)
        self.assertEqual(unfilled.click_count, 0)

    def test_like_clicks_the_latest_unliked_post(self) -> None:
        latest = FakeFavoriteButton()
        older = FakeFavoriteButton()
        page = FakePage([latest, older])

        liked, status = RoomEngagementDriver()._like(page)

        self.assertTrue(liked)
        self.assertEqual(status, "liked")
        self.assertEqual(latest.click_count, 1)
        self.assertEqual(older.click_count, 0)

    def test_engage_sets_navigation_timeout_before_opening_candidate(self) -> None:
        class FakeNavigationPage:
            url = "https://room.rakuten.co.jp/room_example/items"

            def __init__(self) -> None:
                self.default_timeout = None
                self.navigation_timeout = None
                self.goto_kwargs = None
                self.load_state_calls = []

            def set_default_timeout(self, timeout: int) -> None:
                self.default_timeout = timeout

            def set_default_navigation_timeout(self, timeout: int) -> None:
                self.navigation_timeout = timeout

            def goto(self, url: str, **kwargs) -> None:
                self.goto_kwargs = {"url": url, **kwargs}

            def wait_for_load_state(self, state: str, **kwargs) -> None:
                self.load_state_calls.append({"state": state, **kwargs})

        class FastDriver(RoomEngagementDriver):
            def _assert_safe_page(self, _page) -> None:
                pass

            def _can_follow(self, _page):
                return True, "follow_available"

            def _can_like_latest(self, _page):
                return True, "latest_unliked"

            def _follow(self, _page):
                return True, "followed"

            def _like(self, _page):
                return True, "liked"

        page = FakeNavigationPage()
        candidate = candidate_from_room_url("https://room.rakuten.co.jp/room_example/items")
        self.assertIsNotNone(candidate)

        FastDriver(timeout_ms=1234).engage(
            page,
            candidate,
            need_follow=True,
            need_like=True,
        )

        self.assertEqual(page.default_timeout, 1234)
        self.assertEqual(page.navigation_timeout, 60_000)
        self.assertEqual(
            page.goto_kwargs,
            {
                "url": "https://room.rakuten.co.jp/room_example/items",
                "wait_until": "commit",
                "timeout": 60_000,
            },
        )
        self.assertEqual(
            page.load_state_calls,
            [
                {"state": "domcontentloaded", "timeout": 1234},
                {"state": "domcontentloaded", "timeout": 1234},
            ],
        )

    def test_engage_continues_when_domcontentloaded_is_delayed(self) -> None:
        class DelayedNavigationPage:
            url = "https://room.rakuten.co.jp/room_example/items"

            def set_default_timeout(self, _timeout: int) -> None:
                pass

            def set_default_navigation_timeout(self, _timeout: int) -> None:
                pass

            def goto(self, _url: str, **_kwargs) -> None:
                pass

            def wait_for_load_state(self, _state: str, **_kwargs) -> None:
                raise TimeoutError

        class FastDriver(RoomEngagementDriver):
            def _assert_safe_page(self, _page) -> None:
                pass

            def _can_follow(self, _page):
                return True, "follow_available"

            def _can_like_latest(self, _page):
                return True, "latest_unliked"

            def _follow(self, _page):
                return True, "followed"

            def _like(self, _page):
                return True, "liked"

        candidate = candidate_from_room_url("https://room.rakuten.co.jp/room_example/items")
        self.assertIsNotNone(candidate)

        result = FastDriver().engage(
            DelayedNavigationPage(),
            candidate,
            need_follow=True,
            need_like=True,
        )

        self.assertTrue(result.followed)
        self.assertTrue(result.liked)

    def test_engage_skips_already_following_candidate_without_like(self) -> None:
        class PolicyDriver(RoomEngagementDriver):
            def _prepare_page(self, _page) -> None:
                pass

            def _goto(self, _page, _url) -> None:
                pass

            def _assert_safe_page(self, _page) -> None:
                pass

            def _can_follow(self, _page):
                return False, "already_following"

            def _like(self, _page):
                raise AssertionError("like must not run for an existing follow")

        candidate = candidate_from_room_url("https://room.rakuten.co.jp/room_example/items")
        self.assertIsNotNone(candidate)

        result = PolicyDriver().engage(object(), candidate, need_follow=True, need_like=True)

        self.assertFalse(result.followed)
        self.assertFalse(result.liked)
        self.assertEqual(result.follow_status, "already_following")
        self.assertEqual(result.like_status, "not_new_follow")

    def test_engage_checks_latest_like_before_following(self) -> None:
        class PolicyDriver(RoomEngagementDriver):
            def _prepare_page(self, _page) -> None:
                pass

            def _goto(self, _page, _url) -> None:
                pass

            def _assert_safe_page(self, _page) -> None:
                pass

            def _can_follow(self, _page):
                return True, "follow_available"

            def _can_like_latest(self, _page):
                return False, "latest_already_liked"

            def _follow(self, _page):
                raise AssertionError("follow must not run without an eligible paired like")

        candidate = candidate_from_room_url("https://room.rakuten.co.jp/room_example/items")
        self.assertIsNotNone(candidate)

        result = PolicyDriver().engage(object(), candidate, need_follow=True, need_like=True)

        self.assertFalse(result.followed)
        self.assertFalse(result.liked)
        self.assertEqual(result.follow_status, "follow_deferred")
        self.assertEqual(result.like_status, "latest_already_liked")

    def test_engage_preserves_follow_for_fail_closed_resume_when_like_raises(self) -> None:
        class PolicyDriver(RoomEngagementDriver):
            def _prepare_page(self, _page) -> None:
                pass

            def _goto(self, _page, _url) -> None:
                pass

            def _assert_safe_page(self, _page) -> None:
                pass

            def _can_follow(self, _page):
                return True, "follow_available"

            def _can_like_latest(self, _page):
                return True, "latest_unliked"

            def _follow(self, _page):
                return True, "followed"

            def _like(self, _page):
                raise TimeoutError("simulated detached latest-post control")

        candidate = candidate_from_room_url("https://room.rakuten.co.jp/room_example/items")
        self.assertIsNotNone(candidate)

        result = PolicyDriver().engage(object(), candidate, need_follow=True, need_like=True)

        self.assertTrue(result.followed)
        self.assertFalse(result.liked)
        self.assertEqual(result.follow_status, "followed")
        self.assertEqual(result.like_status, "like_exception_TimeoutError")

    def test_discover_candidates_sets_search_navigation_timeout(self) -> None:
        class EmptyLocator:
            @property
            def first(self):
                return self

            def is_visible(self) -> bool:
                return False

            def count(self) -> int:
                return 0

        class FakeDiscoveryPage:
            url = "https://room.rakuten.co.jp/search/user?keyword=test"

            def __init__(self) -> None:
                self.default_timeout = None
                self.navigation_timeout = None
                self.goto_kwargs = None

            def set_default_timeout(self, timeout: int) -> None:
                self.default_timeout = timeout

            def set_default_navigation_timeout(self, timeout: int) -> None:
                self.navigation_timeout = timeout

            def goto(self, url: str, **kwargs) -> None:
                self.goto_kwargs = {"url": url, **kwargs}

            def locator(self, _selector: str):
                return EmptyLocator()

            def evaluate(self, _script: str) -> None:
                pass

            def wait_for_timeout(self, _milliseconds: int) -> None:
                pass

        page = FakeDiscoveryPage()
        with patch("room_engagement_worker.discover_candidates_from_api", return_value=[]):
            discover_candidates(page, ["https://room.rakuten.co.jp/search/user?keyword=test"])

        self.assertEqual(page.default_timeout, 8_000)
        self.assertEqual(page.navigation_timeout, 8_000)
        self.assertEqual(
            page.goto_kwargs,
            {
                "url": "https://room.rakuten.co.jp/search/user?keyword=test",
                "wait_until": "domcontentloaded",
                "timeout": 8_000,
            },
        )

    def test_discover_candidates_falls_back_when_api_results_are_excluded(self) -> None:
        class EmptyLocator:
            @property
            def first(self):
                return self

            def is_visible(self) -> bool:
                return False

            def count(self) -> int:
                return 0

            def nth(self, _index: int):
                return self

        class Link:
            def get_attribute(self, name: str):
                return "/room_new/items" if name == "href" else None

            def inner_text(self) -> str:
                return "New User"

        class LinkLocator:
            def count(self) -> int:
                return 1

            def nth(self, _index: int):
                return Link()

        class FakeDiscoveryPage:
            url = "https://room.rakuten.co.jp/search/user?keyword=test"

            def set_default_timeout(self, _timeout: int) -> None:
                pass

            def set_default_navigation_timeout(self, _timeout: int) -> None:
                pass

            def goto(self, url: str, **_kwargs) -> None:
                self.url = url

            def locator(self, selector: str):
                if selector == 'a[href*="/items"]':
                    return LinkLocator()
                return EmptyLocator()

            def evaluate(self, _script: str) -> None:
                pass

            def wait_for_timeout(self, _milliseconds: int) -> None:
                pass

        stale = candidate_from_room_url("https://room.rakuten.co.jp/room_old/items")
        self.assertIsNotNone(stale)
        page = FakeDiscoveryPage()
        with patch("room_engagement_worker.discover_candidates_from_api", return_value=[stale]):
            candidates = discover_candidates(
                page,
                ["https://room.rakuten.co.jp/search/user?keyword=test"],
                exclude_ids={"room_old"},
            )

        self.assertEqual([candidate.id for candidate in candidates], ["room_new"])

    def test_discover_candidates_stops_after_enough_fresh_api_candidates(self) -> None:
        class FakeDiscoveryPage:
            def set_default_timeout(self, _timeout: int) -> None:
                pass

            def set_default_navigation_timeout(self, _timeout: int) -> None:
                pass

        first = [
            candidate_from_room_url(f"https://room.rakuten.co.jp/room_new_{index}/items")
            for index in range(3)
        ]
        page = FakeDiscoveryPage()
        with patch(
            "room_engagement_worker.discover_candidates_from_api",
            return_value=[candidate for candidate in first if candidate is not None],
        ) as api:
            candidates = discover_candidates(
                page,
                ["https://room.rakuten.co.jp/search/user?keyword=one", "https://room.rakuten.co.jp/search/user?keyword=two"],
                max_candidates=2,
            )

        self.assertEqual(len(candidates), 3)
        api.assert_called_once()

    def test_loads_current_routine_candidates_and_search_links(self) -> None:
        candidates, search_urls = load_routine_sources()
        self.assertGreaterEqual(len(candidates), 50)
        self.assertGreaterEqual(len(search_urls), 30)
        self.assertEqual(len({item.id for item in candidates}), len(candidates))
        self.assertIn("keyword=%E3%83%99%E3%83%93%E3%83%BC%E3%82%AB%E3%83%BC", search_urls[0])
        self.assertTrue(any("keyword=%E5%87%BA%E7%94%A3%E6%BA%96%E5%82%99" in url for url in search_urls))

    def test_extract_json_constant_rejects_missing_data(self) -> None:
        self.assertEqual(extract_json_constant('const ITEMS = [{"id": 1}];', "ITEMS"), [{"id": 1}])
        with self.assertRaises(ValueError):
            extract_json_constant("const OTHER = [];", "ITEMS")

    def test_routine_date_rolls_over_at_five(self) -> None:
        self.assertEqual(routine_date(datetime(2026, 7, 5, 4, 59)), "2026-07-04")
        self.assertEqual(routine_date(datetime(2026, 7, 5, 5, 0)), "2026-07-05")

    def test_progress_counts_unique_candidates_and_merges_retries(self) -> None:
        events = [
            {"routine_date": "2026-07-05", "candidate_id": "a", "followed": True, "liked": False},
            {"routine_date": "2026-07-05", "candidate_id": "a", "followed": False, "liked": True},
            {"routine_date": "2026-07-05", "candidate_id": "b", "followed": True, "liked": True},
            {"routine_date": "2026-07-04", "candidate_id": "c", "followed": True, "liked": True},
        ]
        progress = progress_by_candidate(events, day="2026-07-05")
        self.assertEqual(progress_totals(progress), (2, 2))
        self.assertEqual(set(progress), {"a", "b"})

    def test_daily_queue_reuses_prior_day_candidates_but_skips_today_complete(self) -> None:
        candidates = [
            RoutineCandidate(id="a", name="A", url="https://room.rakuten.co.jp/a/items"),
            RoutineCandidate(id="b", name="B", url="https://room.rakuten.co.jp/b/items"),
            RoutineCandidate(id="c", name="C", url="https://room.rakuten.co.jp/c/items"),
        ]
        today_progress = {
            "a": {"followed": True, "liked": True},
            "b": {"followed": True, "liked": False},
        }

        queue = build_daily_candidate_queue(candidates, today_progress)

        self.assertEqual([candidate.id for candidate in queue], ["b", "c"])

    def test_daily_queue_skips_same_day_non_follow_attempt_and_historical_follow(self) -> None:
        candidates = [
            RoutineCandidate(id="attempted", name="Attempted", url="https://room.rakuten.co.jp/attempted/items"),
            RoutineCandidate(id="partial", name="Partial", url="https://room.rakuten.co.jp/partial/items"),
            RoutineCandidate(id="historical", name="Historical", url="https://room.rakuten.co.jp/historical/items"),
            RoutineCandidate(id="fresh", name="Fresh", url="https://room.rakuten.co.jp/fresh/items"),
        ]
        today_progress = {
            "attempted": {"followed": False, "liked": False},
            "partial": {"followed": True, "liked": False},
        }

        queue = build_daily_candidate_queue(
            candidates,
            today_progress,
            excluded_ids={"historical", "partial"},
        )

        self.assertEqual([candidate.id for candidate in queue], ["partial", "fresh"])

    def test_followed_candidate_ids_uses_status_and_boolean_evidence(self) -> None:
        events = [
            {"candidate_id": "new", "followed": True, "follow_status": "followed"},
            {"candidate_id": "existing", "followed": False, "follow_status": "already_following"},
            {"candidate_id": "unavailable", "followed": False, "follow_status": "not_followed"},
            {"candidate_id": "", "followed": True, "follow_status": "followed"},
        ]

        self.assertEqual(followed_candidate_ids(events), {"new", "existing"})

    def test_event_candidate_restores_dynamic_partial_for_same_day_resume(self) -> None:
        events = [
            {
                "routine_date": "2026-11-16",
                "candidate_id": "room_dynamic",
                "room_url": "https://room.rakuten.co.jp/room_dynamic/items",
            },
            {
                "routine_date": "2026-11-15",
                "candidate_id": "room_old",
                "room_url": "https://room.rakuten.co.jp/room_old/items",
            },
        ]

        candidates = candidates_from_events(events, day="2026-11-16")

        self.assertEqual([candidate.id for candidate in candidates], ["room_dynamic"])

    def test_follow_goal_reached_selects_only_recorded_partial_pair(self) -> None:
        fresh = RoutineCandidate(
            id="fresh",
            name="Fresh",
            url="https://room.rakuten.co.jp/fresh/items",
        )
        partial = RoutineCandidate(
            id="partial",
            name="Partial",
            url="https://room.rakuten.co.jp/partial/items",
        )
        progress = {
            "partial": {"followed": True, "liked": False},
        }

        candidate = next_candidate_for_goals(
            [fresh, partial],
            attempted=set(),
            progress=progress,
            followed=15,
            goal=15,
        )

        self.assertEqual(candidate, partial)

    def test_completed_progress_resume_releases_owner_and_normalizes_evidence(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "operations.db"
            heartbeat = root / "engagement-heartbeat.json"
            now = datetime(2026, 11, 16, 5, 45, tzinfo=JST)
            self.assertEqual(start_gate(database, now=now, goal=15)["status"], "allowed")
            for index in range(15):
                record_action(
                    database,
                    routine_date="2026-11-16",
                    candidate_id=f"room_{index}",
                    room_url=f"https://room.rakuten.co.jp/room_{index}/items",
                    followed=True,
                    liked=True,
                    follow_status="followed",
                    like_status="liked",
                    now=now,
                )

            with patch.object(engagement_worker, "DATABASE_PATH", database), \
                 patch.object(engagement_worker, "HEARTBEAT_PATH", heartbeat), \
                 patch.object(engagement_worker, "configure_logging"), \
                 patch.object(engagement_worker, "load_routine_sources", return_value=([], [])), \
                 patch.object(engagement_worker, "read_ledger", return_value=[]), \
                 patch.object(engagement_worker, "write_and_publish_summary", return_value=True) as publish:
                result = engagement_worker.run(
                    apply=True,
                    goal=15,
                    min_delay=0,
                    max_delay=0,
                    headless=True,
                    now=now,
                )

            self.assertEqual(result, 0)
            with closing(sqlite3.connect(database)) as connection:
                row = connection.execute(
                    "SELECT status,followed,liked,owner_id,last_error_code FROM engagement_runs WHERE routine_date=?",
                    ("2026-11-16",),
                ).fetchone()
            self.assertEqual(row, ("completed", 15, 15, None, ""))
            self.assertEqual(json.loads(heartbeat.read_text(encoding="utf-8"))["status"], "completed")
            summary = publish.call_args.args[0]
            self.assertEqual(summary["status"], "completed")
            self.assertEqual(summary["error_code"], "")

    def test_busy_gate_preserves_other_owner_and_does_not_publish(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "operations.db"
            heartbeat = root / "engagement-heartbeat.json"
            started = datetime(2026, 11, 16, 5, 30, tzinfo=JST)
            self.assertEqual(
                start_gate(database, now=started, goal=15, owner="other-owner")["status"],
                "allowed",
            )

            with patch.object(engagement_worker, "DATABASE_PATH", database), \
                 patch.object(engagement_worker, "HEARTBEAT_PATH", heartbeat), \
                 patch.object(engagement_worker, "configure_logging"), \
                 patch.object(engagement_worker, "write_and_publish_summary", return_value=True) as publish:
                result = engagement_worker.run(
                    apply=True,
                    goal=15,
                    min_delay=0,
                    max_delay=0,
                    headless=True,
                    now=datetime(2026, 11, 16, 5, 31, tzinfo=JST),
                )

            self.assertEqual(result, 0)
            with closing(sqlite3.connect(database)) as connection:
                row = connection.execute(
                    "SELECT status,owner_id FROM engagement_runs WHERE routine_date=?",
                    ("2026-11-16",),
                ).fetchone()
            self.assertEqual(row, ("running", "other-owner"))
            self.assertFalse(heartbeat.exists())
            publish.assert_not_called()

    def test_already_complete_gate_keeps_completed_heartbeat(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "operations.db"
            heartbeat = root / "engagement-heartbeat.json"
            now = datetime(2026, 11, 16, 5, 45, tzinfo=JST)
            self.assertEqual(start_gate(database, now=now, goal=15)["status"], "allowed")
            finish(
                database,
                routine_date="2026-11-16",
                followed=15,
                liked=15,
                goal=15,
                status="completed",
                now=now,
            )

            with patch.object(engagement_worker, "DATABASE_PATH", database), \
                 patch.object(engagement_worker, "HEARTBEAT_PATH", heartbeat), \
                 patch.object(engagement_worker, "configure_logging"), \
                 patch.object(engagement_worker, "write_and_publish_summary", return_value=True) as publish:
                result = engagement_worker.run(
                    apply=True,
                    goal=15,
                    min_delay=0,
                    max_delay=0,
                    headless=True,
                    now=now,
                )

            self.assertEqual(result, 0)
            self.assertEqual(json.loads(heartbeat.read_text(encoding="utf-8"))["status"], "completed")
            self.assertEqual(publish.call_args.args[0]["status"], "already_complete")

    def test_candidate_from_room_url_only_accepts_profile_item_pages(self) -> None:
        candidate = candidate_from_room_url("https://room.rakuten.co.jp/room_example/items", "Example")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.id, "room_example")
        self.assertIsNone(candidate_from_room_url("https://example.com/room_example/items"))
        self.assertIsNone(candidate_from_room_url("https://room.rakuten.co.jp/tora_papa/items"))

    def test_candidate_from_search_user_uses_angular_user_model(self) -> None:
        candidate = candidate_from_search_user(
            {"username": "room_example", "fullname": "Example User"}
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.id, "room_example")
        self.assertEqual(candidate.name, "Example User")
        self.assertIsNone(candidate_from_search_user({"fullname": "Missing username"}))


if __name__ == "__main__":
    unittest.main()
