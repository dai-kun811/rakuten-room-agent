from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from local_room_worker import (
    actions_run_is_today,
    append_ledger_event,
    current_post_slot,
    generation_run_candidates,
    load_claimed_post_slots,
    load_claimed_product_types,
    load_reserved_urls,
    parse_post_windows,
    ready_items,
    resolve_post_slot,
    select_candidates_for_slot,
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
        jst = timezone(timedelta(hours=9))
        now = datetime(2026, 7, 5, 8, 0, tzinfo=jst)
        self.assertTrue(actions_run_is_today({"created_at": "2026-07-04T22:00:00Z"}, now))
        self.assertFalse(actions_run_is_today({"created_at": "2026-07-03T22:00:00Z"}, now))
        self.assertFalse(actions_run_is_today({"created_at": "2026-07-04T16:52:00Z"}, now))

    def test_generation_candidates_require_today_and_sort_newest_first(self) -> None:
        jst = timezone(timedelta(hours=9))
        now = datetime(2026, 8, 16, 12, 0, tzinfo=jst)
        runs = [
            {
                "id": 1,
                "conclusion": "success",
                "created_at": "2026-08-14T22:30:00Z",
            },
            {
                "id": 2,
                "conclusion": "success",
                "created_at": "2026-08-15T22:20:00Z",
            },
            {
                "id": 3,
                "conclusion": "success",
                "created_at": "2026-08-15T22:40:00Z",
            },
            {
                "id": 4,
                "conclusion": "failure",
                "created_at": "2026-08-15T22:50:00Z",
            },
        ]

        self.assertEqual(
            [run["id"] for run in generation_run_candidates(runs, require_today=True, now=now)],
            [3, 2],
        )

    def test_forced_slot_supports_safe_same_day_recovery(self) -> None:
        windows = parse_post_windows("morning:8-11,noon:11-16,evening:17-22")
        now = datetime(2026, 7, 6, 16, 30)

        self.assertEqual(
            resolve_post_slot(now, override="morning", windows=windows),
            "2026-07-06:morning",
        )
        with self.assertRaises(ValueError):
            resolve_post_slot(now, override="night", windows=windows)

    def test_forced_slot_can_backfill_an_explicit_post_date(self) -> None:
        windows = parse_post_windows("morning:8-11,noon:11-16,evening:17-22")
        now = datetime(2026, 7, 17, 1, 30)

        self.assertEqual(
            resolve_post_slot(
                now,
                override="evening",
                post_date="2026-07-16",
                windows=windows,
            ),
            "2026-07-16:evening",
        )

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

    def test_retry_failed_detail_reopens_matching_slot_only(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.jsonl"
            events = [
                {
                    "post_slot": "2026-07-06:morning",
                    "status": "failed",
                    "detail": "TimeoutError",
                },
                {
                    "post_slot": "2026-07-06:noon",
                    "status": "failed",
                    "detail": "投稿後の完了表示を確認できませんでした。",
                },
            ]
            path.write_text(
                "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
                encoding="utf-8",
            )

            self.assertEqual(
                load_claimed_post_slots(
                    path,
                    retry_failed_details={"TimeoutError"},
                ),
                {"2026-07-06:noon"},
            )

    def test_ready_items_excludes_review_and_incomplete_rows(self) -> None:
        report = {
            "items": [
                {"status": "ready", "product_url": "https://example.com/a", "body": "本文"},
                {"status": "needs_review", "product_url": "https://example.com/b", "body": "本文"},
                {"status": "ready", "product_url": "", "body": "本文"},
            ]
        }
        self.assertEqual([item["product_url"] for item in ready_items(report)], ["https://example.com/a"])

    def test_ready_items_selects_only_the_assigned_post_slot(self) -> None:
        report = {
            "items": [
                {
                    "status": "ready",
                    "post_slot": "morning",
                    "product_url": "https://example.com/morning",
                    "body": "朝",
                },
                {
                    "status": "ready",
                    "post_slot": "noon",
                    "product_url": "https://example.com/noon",
                    "body": "昼",
                },
                {
                    "status": "ready",
                    "product_url": "https://example.com/unassigned",
                    "body": "未割当",
                },
            ]
        }

        self.assertEqual(
            [item["product_url"] for item in ready_items(report, post_slot="noon")],
            ["https://example.com/noon"],
        )

    def test_regeneration_promotes_unused_item_from_claimed_earlier_slot(self) -> None:
        report = {
            "items": [
                {"status": "ready", "post_slot": "morning", "product_url": "https://example.com/discovery", "body": "知育"},
                {"status": "ready", "post_slot": "noon", "product_url": "https://example.com/consumable", "body": "日用品"},
            ]
        }

        candidates = select_candidates_for_slot(
            report,
            slot="2026-09-26:noon",
            reserved_urls=set(),
            claimed_post_slots={"2026-09-26:morning"},
        )

        self.assertEqual(candidates[0]["product_url"], "https://example.com/discovery")

    def test_same_report_keeps_assigned_item_when_earlier_url_was_posted(self) -> None:
        report = {
            "items": [
                {"status": "ready", "post_slot": "morning", "product_url": "https://example.com/morning", "body": "朝"},
                {"status": "ready", "post_slot": "noon", "product_url": "https://example.com/noon", "body": "昼"},
            ]
        }

        candidates = select_candidates_for_slot(
            report,
            slot="2026-09-26:noon",
            reserved_urls={"https://example.com/morning"},
            claimed_post_slots={"2026-09-26:morning"},
        )

        self.assertEqual([item["product_url"] for item in candidates], ["https://example.com/noon"])

    def test_regeneration_prefers_a_type_not_already_claimed_today(self) -> None:
        report = {
            "items": [
                {"status": "ready", "post_slot": "morning", "product_url": "https://example.com/wipes", "product_type": "wipes", "body": "朝"},
                {"status": "ready", "post_slot": "noon", "product_url": "https://example.com/blocks", "product_type": "magnetic_blocks", "body": "昼"},
            ]
        }

        candidates = select_candidates_for_slot(
            report,
            slot="2026-09-26:noon",
            reserved_urls=set(),
            claimed_post_slots={"2026-09-26:morning"},
            claimed_product_types={"wipes"},
        )

        self.assertEqual(candidates[0]["product_url"], "https://example.com/blocks")

    def test_claimed_product_types_uses_latest_same_day_events(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.jsonl"
            events = [
                {"post_slot": "2026-09-25:evening", "product_type": "wipes", "status": "posted"},
                {"post_slot": "2026-09-26:morning", "product_type": "wipes", "status": "reserved"},
                {"post_slot": "2026-09-26:noon", "product_type": "diaper", "status": "failed", "detail": "TimeoutError"},
            ]
            path.write_text(
                "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
                encoding="utf-8",
            )

            self.assertEqual(
                load_claimed_product_types("2026-09-26", path),
                {"wipes", "diaper"},
            )
            self.assertEqual(
                load_claimed_product_types(
                    "2026-09-26",
                    path,
                    retry_failed_details={"TimeoutError"},
                ),
                {"wipes"},
            )

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
