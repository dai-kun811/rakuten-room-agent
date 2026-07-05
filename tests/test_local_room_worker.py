from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from local_room_worker import (
    actions_run_is_today,
    append_ledger_event,
    current_post_slot,
    load_claimed_post_slots,
    load_reserved_urls,
    parse_post_windows,
    ready_items,
)


class LocalRoomWorkerTest(unittest.TestCase):
    def test_post_windows_map_morning_noon_and_evening(self) -> None:
        windows = parse_post_windows("morning:8-11,noon:11-16,evening:17-22")
        self.assertEqual(
            current_post_slot(datetime(2026, 7, 5, 8, 15), windows=windows),
            "2026-07-05:morning",
        )
        self.assertEqual(
            current_post_slot(datetime(2026, 7, 5, 12, 15), windows=windows),
            "2026-07-05:noon",
        )
        self.assertEqual(
            current_post_slot(datetime(2026, 7, 5, 18, 15), windows=windows),
            "2026-07-05:evening",
        )
        self.assertEqual(current_post_slot(datetime(2026, 7, 5, 16, 0), windows=windows), "")

    def test_actions_run_must_be_today_in_local_timezone(self) -> None:
        now = datetime(2026, 7, 5, 8, 0, tzinfo=timezone.utc)
        self.assertTrue(actions_run_is_today({"created_at": "2026-07-05T01:00:00Z"}, now))
        self.assertFalse(actions_run_is_today({"created_at": "2026-07-04T01:00:00Z"}, now))

    def test_claimed_slot_blocks_duplicate_post_in_same_window(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "post_slot": "2026-07-05:morning",
                        "status": "reserved",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(load_claimed_post_slots(path), {"2026-07-05:morning"})

    def test_ready_items_excludes_review_and_incomplete_rows(self) -> None:
        report = {
            "items": [
                {"status": "ready", "product_url": "https://example.com/a", "body": "本文"},
                {"status": "needs_review", "product_url": "https://example.com/b", "body": "本文"},
                {"status": "ready", "product_url": "", "body": "本文"},
            ]
        }
        self.assertEqual([item["product_url"] for item in ready_items(report)], ["https://example.com/a"])

    def test_ledger_reserves_url_before_posting(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.jsonl"
            append_ledger_event(
                {
                    "normalized_url": "https://item.rakuten.co.jp/shop/item/?x=1",
                    "status": "reserved",
                },
                path,
            )
            path.write_text(path.read_text(encoding="utf-8") + "not-json\n", encoding="utf-8")
            self.assertEqual(
                load_reserved_urls(path),
                {"https://item.rakuten.co.jp/shop/item"},
            )

    def test_retry_failed_details_only_reopens_matching_failure(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.jsonl"
            events = [
                {"normalized_url": "https://example.com/retry", "status": "reserved"},
                {
                    "normalized_url": "https://example.com/retry",
                    "status": "failed",
                    "detail": "ModuleNotFoundError",
                },
                {
                    "normalized_url": "https://example.com/keep",
                    "status": "failed",
                    "detail": "投稿後の完了表示を確認できませんでした。",
                },
            ]
            path.write_text(
                "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
                encoding="utf-8",
            )

            reserved = load_reserved_urls(
                path,
                retry_failed_details={"ModuleNotFoundError"},
            )
            self.assertEqual(reserved, {"https://example.com/keep"})

    def test_ledger_event_is_json_without_auth_material(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.jsonl"
            append_ledger_event(
                {"normalized_url": "https://example.com/item", "status": "posted"},
                path,
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "posted")
            self.assertNotIn("cookie", json.dumps(payload).lower())


if __name__ == "__main__":
    unittest.main()
