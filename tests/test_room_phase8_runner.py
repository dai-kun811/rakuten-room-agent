from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from room_operation_contract import POST_SLOTS, SlotStatus
from room_orchestrator_worker import execute
from room_phase8_runner import (
    GenerationFence,
    audit_day,
    dispatch_generation_once,
    next_eligible_slot,
)
from room_state_store import RoomStateStore
from tests.test_room_orchestrator_worker import Gateway, manifest


NOW = datetime(2026, 10, 9, 3, 5, tzinfo=timezone.utc)  # 12:05 JST


class Response:
    def __init__(self, payload=None) -> None:
        self.payload = payload or {}

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


class Session:
    def __init__(self) -> None:
        self.posts = []

    def get(self, url, **kwargs):
        return Response({"commit": {"sha": "a" * 40}})

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs["json"]))
        return Response()


def phase8_manifest() -> dict:
    value = manifest()
    value["routine_date_jst"] = "2026-10-09"
    value["revision"] = 300
    value["recovery_id"] = "production-20261009"
    value["generation_channel"] = "local_fenced_recovery"
    return value


class RoomPhase8RunnerTests(unittest.TestCase):
    def test_dispatch_is_durable_and_happens_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = RoomStateStore(root / "operations.db")
            store.reserve_recovery_control(
                recovery_id="production-20261009", routine_date="2026-10-09",
                old_run_id="37579267266", expected_head_sha="a" * 40,
                revision=300, owner_id="phase8-2026-10-09", now=NOW,
            )
            store.create_slot(
                "2026-10-08", "morning", status=SlotStatus.POSTED,
                normalized_url="https://item.rakuten.co.jp/shop/old", now=NOW,
            )
            fence = GenerationFence(
                "production-20261009", "2026-10-09", "a" * 40, 300,
                "phase8-2026-10-09", root / "recovery",
            )
            session = Session()
            with patch("room_phase8_runner._git_head", return_value="a" * 40):
                self.assertTrue(dispatch_generation_once(
                    session, headers={}, store=store, fence=fence, now=NOW
                ))
                self.assertFalse(dispatch_generation_once(
                    session, headers={}, store=store, fence=fence, now=NOW
                ))
            self.assertEqual(len(session.posts), 1)
            inputs = session.posts[0][1]["inputs"]
            self.assertEqual(inputs["manifest_revision"], "300")
            self.assertIn("shop/old", inputs["posted_history_urls"])
            self.assertEqual(json.loads(fence.control_path.read_text())["status"], "dispatched")

    def test_earliest_due_slot_only_and_uncertain_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = RoomStateStore(Path(temp) / "operations.db")
            value = phase8_manifest()
            store.initialize()
            store.accept_manifest(value, now=NOW)
            self.assertEqual(next_eligible_slot(value, store, NOW), "morning")
            execute(
                manifest=value, slot="morning", active_slots=set(POST_SLOTS), now=NOW,
                apply=True, store=store, gateway=Gateway(),
                legacy_ledger_path=Path(temp) / "ledger.jsonl",
            )
            self.assertEqual(next_eligible_slot(value, store, NOW), "noon")
            noon = store.get_slot("2026-10-09", "noon")
            store.transition_slot(
                "2026-10-09", "noon", expected_status=noon.status,
                expected_version=noon.version, target_status=SlotStatus.CLAIMED, now=NOW,
            )
            store.transition_slot(
                "2026-10-09", "noon", expected_status=SlotStatus.CLAIMED,
                expected_version=noon.version + 1, target_status=SlotStatus.SUBMITTING, now=NOW,
            )
            store.transition_slot(
                "2026-10-09", "noon", expected_status=SlotStatus.SUBMITTING,
                expected_version=noon.version + 2, target_status=SlotStatus.UNCERTAIN, now=NOW,
            )
            with self.assertRaisesRegex(RuntimeError, "UNCERTAIN"):
                next_eligible_slot(value, store, NOW)

    def test_audit_proves_db_attempt_and_ledger_alignment(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = RoomStateStore(root / "operations.db")
            ledger = root / "ledger.jsonl"
            value = phase8_manifest()
            gateway = Gateway()
            evening = datetime(2026, 10, 9, 10, 5, tzinfo=timezone.utc)
            for slot in POST_SLOTS:
                result = execute(
                    manifest=value, slot=slot, active_slots=set(POST_SLOTS), now=evening,
                    apply=True, store=store, gateway=gateway, legacy_ledger_path=ledger,
                )
                self.assertEqual(result["status"], SlotStatus.POSTED.value)
            self.assertEqual(gateway.calls, 3)
            self.assertTrue(audit_day(store, ledger, "2026-10-09")["ok"])
            with ledger.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "post_slot": "2026-10-09:evening", "status": "posted",
                    "normalized_url": "https://item.rakuten.co.jp/shop/wrong",
                }) + "\n")
            self.assertFalse(audit_day(store, ledger, "2026-10-09")["ok"])


if __name__ == "__main__":
    unittest.main()
