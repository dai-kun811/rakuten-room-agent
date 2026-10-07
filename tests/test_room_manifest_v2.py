from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from room_manifest_v2 import build_manifest_v2, candidate_content_hash, write_manifest_v2
from room_operation_contract import ContractError, validate_manifest_v2


def report_item(slot: str, *, status: str = "ready", errors: list[str] | None = None) -> dict:
    quality_errors = list(errors or [])
    return {
        "product_name": f"商品-{slot}",
        "product_url": f"https://item.rakuten.co.jp/shop/{slot}/?scid=test",
        "short_product_label": f"短縮名-{slot}",
        "product_type": "wipes",
        "title": f"タイトル-{slot}",
        "body": f"本文-{slot}",
        "hashtags": ["#育児", "#おしりふき"],
        "status": status,
        "post_slot": slot,
        "review_reasons": quality_errors,
        "recommendation_reason": "補充量を判断しやすい候補",
        "confirmed_features": ["thick"],
        "confirmed_use_cases": ["おむつ替え"],
        "purchase_checkpoints": ["枚数", "個数", "収納場所"],
        "source_evidence": {
            "category": "おしりふき",
            "caption": "厚手 80枚 12個",
            "catchcopy": "おむつ替え用",
            "shop_name": "店舗",
            "search_keyword": "おしりふき",
        },
        "quality": {
            "score": 90,
            "errors": quality_errors,
            "title_evidence_result": "OK",
            "tag_evidence_result": "OK",
            "recommendation_reason_result": "OK",
            "structure_similarity": 0.2,
        },
    }


def report(*items: dict, missing: list[str] | None = None) -> dict:
    return {
        "items": list(items),
        "missing_post_slots": list(missing or []),
    }


class RoomManifestV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 10, 6, 7, 5, tzinfo=timezone.utc)

    def build(self, source: dict, **overrides) -> dict:
        values = {
            "actions_run_id": "12345",
            "head_sha": "abcdef1234567890",
            "revision": 7,
            "generated_at": self.now,
        }
        values.update(overrides)
        return build_manifest_v2(source, **values)

    def test_three_ready_slots_build_valid_manifest(self) -> None:
        payload = self.build(
            report(
                report_item("morning"),
                report_item("noon"),
                report_item("evening"),
            )
        )

        validate_manifest_v2(payload)
        self.assertEqual(payload["routine_date_jst"], "2026-10-06")
        self.assertTrue(all(value["status"] == "ready" for value in payload["slots"].values()))
        self.assertNotIn("scid=", payload["slots"]["morning"]["candidate"]["normalized_url"])

    def test_partial_report_keeps_good_slots_ready(self) -> None:
        payload = self.build(
            report(
                report_item("morning"),
                report_item("noon", status="needs_review", errors=["unsafe claim"]),
                report_item("evening"),
                missing=["noon"],
            )
        )

        self.assertEqual(payload["slots"]["morning"]["status"], "ready")
        self.assertEqual(payload["slots"]["noon"]["status"], "blocked")
        self.assertIn("unsafe claim", payload["slots"]["noon"]["reason"])
        self.assertEqual(payload["slots"]["evening"]["status"], "ready")

    def test_missing_slot_is_blocked_without_blocking_others(self) -> None:
        payload = self.build(
            report(report_item("morning"), report_item("evening"), missing=["noon"])
        )

        self.assertEqual(
            payload["slots"]["noon"],
            {"status": "blocked", "reason": "missing_quality_safe_candidate"},
        )

    def test_duplicate_ready_candidates_block_ambiguous_slot(self) -> None:
        duplicate = report_item("morning")
        duplicate["product_url"] = "https://item.rakuten.co.jp/shop/other/"
        payload = self.build(report(report_item("morning"), duplicate))

        self.assertEqual(
            payload["slots"]["morning"]["reason"],
            "multiple_ready_candidates_for_slot",
        )

    def test_content_hash_changes_with_post_text(self) -> None:
        first = report_item("morning")
        second = dict(first)
        second["body"] = "変更後の本文"

        self.assertNotEqual(candidate_content_hash(first), candidate_content_hash(second))

    def test_writer_replaces_temporary_file_and_leaves_valid_json(self) -> None:
        source = report(
            report_item("morning"),
            report_item("noon"),
            report_item("evening"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = write_manifest_v2(
                Path(temp_dir),
                source,
                actions_run_id="12345",
                head_sha="abcdef1234567890",
                revision=1,
                generated_at=self.now,
            )

            payload = json.loads(path.read_text(encoding="utf-8"))
            validate_manifest_v2(payload)
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_invalid_revision_is_rejected_by_contract(self) -> None:
        with self.assertRaises(ContractError):
            self.build(report(), revision=0)

    def test_recovery_manifest_requires_fenced_channel_and_valid_id(self) -> None:
        payload = self.build(report(), revision=3)
        payload["recovery_id"] = "recovery-20261007-01"
        with self.assertRaises(ContractError):
            validate_manifest_v2(payload)
        payload["generation_channel"] = "local_fenced_recovery"
        validate_manifest_v2(payload)
        payload["recovery_id"] = "short"
        with self.assertRaises(ContractError):
            validate_manifest_v2(payload)


if __name__ == "__main__":
    unittest.main()
