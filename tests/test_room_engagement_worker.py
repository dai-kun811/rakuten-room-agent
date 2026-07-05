from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from room_engagement_worker import (
    candidate_from_room_url,
    extract_json_constant,
    load_routine_sources,
    progress_by_candidate,
    progress_totals,
    routine_date,
)


class RoomEngagementWorkerTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
