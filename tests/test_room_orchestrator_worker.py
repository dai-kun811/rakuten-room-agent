from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from room_operation_contract import SlotStatus
from room_orchestrator import Confirmation
from room_orchestrator_worker import execute, load_fenced_manifest, safe_summary, slot_is_due
from room_state_store import RoomStateStore


NOW = datetime(2026, 10, 7, 10, 0, tzinfo=timezone.utc)


def candidate(slot: str) -> dict:
    value = {
        "product_url": f"https://item.rakuten.co.jp/shop/{slot}",
        "normalized_url": f"https://item.rakuten.co.jp/shop/{slot}",
        "product_name": slot,
        "short_product_label": slot,
        "product_type": "test",
        "title": slot,
        "body": f"{slot}の商品内容を確認した安全な投稿本文です。購入前の確認点も案内します。",
        "hashtags": ["#育児"],
        "recommendation_reason": "test",
        "confirmed_features": [],
        "confirmed_use_cases": [],
        "purchase_checkpoints": [],
        "source_evidence": {key: "" for key in ("category", "caption", "catchcopy", "shop_name", "search_keyword")},
        "quality": {"status": "passed", "score": 90, "errors": [], "title_evidence_result": "OK", "tag_evidence_result": "OK", "recommendation_reason_result": "OK", "structure_similarity": 0.1},
    }
    canonical = {"body": value["body"], "hashtags": value["hashtags"], "normalized_url": value["normalized_url"], "title": value["title"]}
    value["content_hash"] = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return value


def manifest() -> dict:
    return {
        "schema_version": 2,
        "routine_date_jst": "2026-10-07",
        "revision": 100,
        "supersedes_revision": None,
        "actions_run_id": "123",
        "head_sha": "a" * 40,
        "generated_at": "2026-10-07T07:00:00+09:00",
        "slots": {slot: {"status": "ready", "candidate": candidate(slot)} for slot in ("morning", "noon", "evening")},
    }


class Gateway:
    def __init__(self) -> None:
        self.calls = 0

    def submit(self, value, *, before_submit, after_submit):
        self.calls += 1
        before_submit()
        after_submit()
        return Confirmation.PRESENT


class RoomOrchestratorWorkerTests(unittest.TestCase):
    def test_due_times_are_jst_and_evening_is_not_early(self) -> None:
        self.assertTrue(slot_is_due("morning", NOW))
        self.assertTrue(slot_is_due("noon", NOW))
        self.assertTrue(slot_is_due("evening", NOW))
        before_evening = datetime(2026, 10, 7, 9, 59, tzinfo=timezone.utc)
        self.assertFalse(slot_is_due("evening", before_evening))

    def test_dry_run_does_not_create_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "operations.db"
            result = execute(manifest=manifest(), slot="morning", active_slots={"morning"}, now=NOW, apply=False, store=RoomStateStore(path))
            self.assertTrue(result["would_execute"])
            self.assertFalse(path.exists())

    def test_non_owned_slot_is_rejected_before_state_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "operations.db"
            with self.assertRaisesRegex(RuntimeError, "not owned"):
                execute(manifest=manifest(), slot="noon", active_slots={"morning"}, now=NOW, apply=True, store=RoomStateStore(path), gateway=Gateway())
            self.assertFalse(path.exists())

    def test_apply_posts_once_and_rerun_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            gateway = Gateway()
            store = RoomStateStore(Path(temp) / "operations.db")
            ledger = Path(temp) / "ledger.jsonl"
            first = execute(manifest=manifest(), slot="morning", active_slots={"morning"}, now=NOW, apply=True, store=store, gateway=gateway, legacy_ledger_path=ledger)
            second = execute(manifest=manifest(), slot="morning", active_slots={"morning"}, now=NOW, apply=True, store=store, gateway=gateway, legacy_ledger_path=ledger)
            self.assertEqual(first["status"], SlotStatus.POSTED.value)
            self.assertEqual(second["status"], SlotStatus.POSTED.value)
            self.assertEqual(gateway.calls, 1)

    def test_safe_summary_does_not_include_body_or_url(self) -> None:
        encoded = json.dumps(safe_summary(manifest(), {"morning"}, NOW), ensure_ascii=False)
        self.assertNotIn("投稿本文", encoded)
        self.assertNotIn("item.rakuten.co.jp", encoded)

    def test_local_fenced_manifest_rejects_wrong_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "manifest.json"
            value = manifest()
            value["recovery_id"] = "recovery-20261007-01"
            value["generation_channel"] = "local_fenced_recovery"
            path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            loaded = load_fenced_manifest(
                path,
                expected_head_sha="a" * 40,
                expected_recovery_id="recovery-20261007-01",
            )
            self.assertEqual(loaded["recovery_id"], "recovery-20261007-01")
            with self.assertRaisesRegex(RuntimeError, "HEAD"):
                load_fenced_manifest(
                    path,
                    expected_head_sha="b" * 40,
                    expected_recovery_id="recovery-20261007-01",
                )


if __name__ == "__main__":
    unittest.main()
