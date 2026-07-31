from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from room_engagement_worker import (
    RoomEngagementDriver,
    RoutineCandidate,
    build_daily_candidate_queue,
    candidate_from_room_url,
    candidate_from_search_user,
    discover_candidates,
    discover_candidates_from_api,
    extract_json_constant,
    load_routine_sources,
    progress_by_candidate,
    progress_totals,
    routine_date,
    submit_user_search,
)


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

    def test_like_waits_for_cards_and_clicks_an_unfilled_favorite(self) -> None:
        filled = FakeFavoriteButton(filled=True)
        unfilled = FakeFavoriteButton()
        page = FakePage([filled, unfilled])

        liked, status = RoomEngagementDriver()._like(page)

        self.assertTrue(liked)
        self.assertEqual(status, "liked")
        self.assertIn("favorite-filled", unfilled.icon_class)

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

    def test_loads_current_routine_candidates_and_search_links(self) -> None:
        candidates, search_urls = load_routine_sources()
        self.assertGreaterEqual(len(candidates), 50)
        self.assertGreaterEqual(len(search_urls), 30)
        self.assertEqual(len({item.id for item in candidates}), len(candidates))
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
