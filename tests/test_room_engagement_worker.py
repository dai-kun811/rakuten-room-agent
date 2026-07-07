from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from room_engagement_worker import (
    RoomEngagementDriver,
    candidate_from_room_url,
    candidate_from_search_user,
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

    def wait_for_load_state(self, state: str) -> None:
        self.waited_for = state

    def wait_for_timeout(self, _milliseconds: int) -> None:
        pass


class RoomEngagementWorkerTest(unittest.TestCase):
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

    def test_loads_current_routine_candidates_and_search_links(self) -> None:
        candidates, search_urls = load_routine_sources()
        self.assertGreaterEqual(len(candidates), 50)
        self.assertTrue(search_urls)
        self.assertEqual(len({item.id for item in candidates}), len(candidates))

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
