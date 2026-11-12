from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date, datetime
from datetime import timedelta
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from room_operation_contract import JST
from room_x_room_evidence import canonical_item_url, detail_urls_from_hrefs, href_contains_product
from room_x_schedule_worker import (
    XScheduleError,
    build_promo_copy,
    in_publish_window,
    posted_database_evidence,
    prepare_daily_plan,
    release_promotion_from_evidence,
)
from room_x_content_planner import plan_x_content, x_weighted_length


def candidate(product_url: str, label: str = "ベビー保湿剤") -> dict[str, object]:
    return {
        "product_url": product_url,
        "normalized_url": product_url,
        "product_name": label,
        "short_product_label": label,
        "product_type": "baby_care",
        "title": f"{label}を選ぶ前に",
        "purchase_checkpoints": "成分 使う部位 対象月齢",
        "quality": {"status": "passed", "score": 95, "errors": []},
        "content_hash": "a" * 64,
    }


def manifest(day: date) -> dict[str, object]:
    return {
        "routine_date_jst": day.isoformat(),
        "slots": {
            "morning": {
                "status": "ready",
                "candidate": candidate("https://item.rakuten.co.jp/shop/item"),
            },
            "noon": {
                "status": "ready",
                "candidate": candidate("https://item.rakuten.co.jp/shop/item-2"),
            },
            "evening": {
                "status": "ready",
                "candidate": candidate("https://item.rakuten.co.jp/shop/item-3"),
            },
        },
    }


class RoomXScheduleWorkerTest(unittest.TestCase):
    def test_reviewed_catalog_supplies_unique_visual_ready_cards_for_four_weeks(self) -> None:
        catalog = json.loads(
            Path("config/room-x-helpful-cards.json").read_text(encoding="utf-8")
        )
        for pattern in (
            "micro_hack_v1",
            "saveable_checklist_v1",
            "before_after_setup_v1",
            "conversation_prompt_v1",
        ):
            self.assertGreaterEqual(
                sum(card["pattern_id"] == pattern for card in catalog["cards"]),
                5,
            )
        recent_hashes: list[str] = []
        start = date(2026, 11, 16)
        planned = []
        for offset in range(28):
            day = start + timedelta(days=offset)
            if day.weekday() not in {0, 2, 4, 6}:
                continue
            snapshot = {
                "version": 1,
                "plan_date": day.isoformat(),
                "recent_promoted_product_ids": [],
                "recent_helpful_content_hashes": list(recent_hashes),
                "helpful_cards": catalog["cards"],
            }
            plan = plan_x_content(snapshot)
            recent_hashes.append(plan["helpful_content_hash"])
            planned.append(plan["card"]["card_id"])
            self.assertLessEqual(x_weighted_length(plan["copy"]), 280)
            source = next(
                item for item in catalog["cards"] if item["card_id"] == plan["card"]["card_id"]
            )
            self.assertTrue(Path(source["visual_path"]).is_file())
        self.assertEqual(len(planned), 16)
        self.assertEqual(len(set(planned)), 16)

    def test_promotion_plan_preselects_morning_before_room_publication(self) -> None:
        day = date(2026, 11, 14)
        with tempfile.TemporaryDirectory(dir=Path(".local/test-tmp")) as temporary:
            plan = prepare_daily_plan(
                now=datetime(2026, 11, 14, 7, 10, tzinfo=JST),
                manifest=manifest(day),
                cards_payload=None,
                state_root=Path(temporary),
            )
        self.assertEqual(plan["binding"]["slot"], "morning")
        self.assertTrue(plan["prepared_before_room_publication"])
        self.assertIn("#PR", plan["binding"]["promo_copy"])

    def test_promo_copy_uses_candidate_label_and_checkpoints(self) -> None:
        copy = build_promo_copy(candidate("https://item.rakuten.co.jp/shop/item"))
        self.assertIn("ベビー保湿剤", copy)
        self.assertIn("成分・使う部位・対象月齢", copy)
        self.assertTrue(copy.endswith("#PR"))

    def test_promotion_window_is_only_first_fifteen_minutes_after_room_slot(self) -> None:
        day = date(2026, 11, 14)
        with tempfile.TemporaryDirectory(dir=Path(".local/test-tmp")) as temporary:
            plan = prepare_daily_plan(
                now=datetime(2026, 11, 14, 7, 10, tzinfo=JST),
                manifest=manifest(day),
                cards_payload=None,
                state_root=Path(temporary),
            )
        self.assertTrue(in_publish_window(datetime(2026, 11, 14, 8, 5, tzinfo=JST), plan))
        self.assertFalse(in_publish_window(datetime(2026, 11, 14, 8, 20, tzinfo=JST), plan))

    def test_promotion_plan_cannot_be_created_after_selected_room_slot(self) -> None:
        day = date(2026, 11, 14)
        with tempfile.TemporaryDirectory(dir=Path(".local/test-tmp")) as temporary:
            with self.assertRaisesRegex(XScheduleError, "before its ROOM slot"):
                prepare_daily_plan(
                    now=datetime(2026, 11, 14, 8, 1, tzinfo=JST),
                    manifest=manifest(day),
                    cards_payload=None,
                    state_root=Path(temporary),
                )

    def test_posted_database_evidence_requires_matching_posted_attempt(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(".local/test-tmp")) as temporary:
            database = Path(temporary) / "operations.db"
            with closing(sqlite3.connect(database)) as connection:
                connection.executescript(
                    """
                    CREATE TABLE slots(routine_date TEXT, slot TEXT, status TEXT, normalized_url TEXT);
                    CREATE TABLE post_attempts(attempt_id TEXT, routine_date TEXT, slot TEXT, status TEXT, created_at TEXT);
                    INSERT INTO slots VALUES('2026-11-14','morning','posted','https://item.rakuten.co.jp/shop/item');
                    INSERT INTO post_attempts VALUES('attempt-1','2026-11-14','morning','posted','2026-11-14T08:00:00+09:00');
                    """
                )
                connection.commit()
            evidence = posted_database_evidence(
                database,
                day="2026-11-14",
                slot="morning",
                product_url="https://item.rakuten.co.jp/shop/item",
            )
        self.assertEqual(evidence["posted_evidence_id"], "attempt-1")

    def test_release_requires_exact_authenticated_room_detail_product(self) -> None:
        day = date(2026, 11, 14)
        with tempfile.TemporaryDirectory(dir=Path(".local/test-tmp")) as temporary:
            root = Path(temporary)
            plan = prepare_daily_plan(
                now=datetime(2026, 11, 14, 7, 10, tzinfo=JST),
                manifest=manifest(day),
                cards_payload=None,
                state_root=root,
            )
            database = root / "operations.db"
            with closing(sqlite3.connect(database)) as connection:
                connection.executescript(
                    """
                    CREATE TABLE slots(routine_date TEXT, slot TEXT, status TEXT, normalized_url TEXT);
                    CREATE TABLE post_attempts(attempt_id TEXT, routine_date TEXT, slot TEXT, status TEXT, created_at TEXT);
                    INSERT INTO slots VALUES('2026-11-14','morning','posted','https://item.rakuten.co.jp/shop/item');
                    INSERT INTO post_attempts VALUES('attempt-1','2026-11-14','morning','posted','2026-11-14T08:00:00+09:00');
                    """
                )
                connection.commit()
            with self.assertRaisesRegex(XScheduleError, "ROOM detail evidence"):
                release_promotion_from_evidence(
                    plan,
                    database_path=database,
                    room_detail_lookup=lambda _: {
                        "room_detail_url": "https://room.rakuten.co.jp/tora_papa/1700000000000000",
                        "product_url": "https://item.rakuten.co.jp/shop/other",
                    },
                    state_root=root,
                )

    def test_room_href_helpers_accept_only_owned_detail_and_exact_item(self) -> None:
        details = detail_urls_from_hrefs(
            [
                "/tora_papa/items",
                "/tora_papa/1700000000000000",
                "https://room.rakuten.co.jp/other/1700000000000001",
            ]
        )
        self.assertEqual(details, ["https://room.rakuten.co.jp/tora_papa/1700000000000000"])
        self.assertTrue(
            href_contains_product(
                "https://example.com/click?pc=https%3A%2F%2Fitem.rakuten.co.jp%2FShop%2FItem%2F",
                "https://item.rakuten.co.jp/shop/item",
            )
        )
        self.assertEqual(
            canonical_item_url("https://item.rakuten.co.jp/Shop/Item/?scid=x"),
            "https://item.rakuten.co.jp/shop/item",
        )


if __name__ == "__main__":
    unittest.main()
