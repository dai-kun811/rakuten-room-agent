from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from room_engagement_state import audit, progress, record_action, sanitized_heartbeat, start_gate
from room_operation_contract import JST


class EngagementStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.db = Path(self.temp.name) / "operations.db"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_start_gate_refuses_delayed_operation_after_cutoff(self) -> None:
        result = start_gate(self.db, now=datetime(2026, 10, 24, 7, 31, tzinfo=JST))
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "cutoff_expired")
        self.assertEqual(audit(self.db, routine_date="2026-10-24", now=datetime(2026, 10, 24, 7, 40, tzinfo=JST))["errors"], [])

    def test_start_gate_is_not_due_before_window(self) -> None:
        result = start_gate(self.db, now=datetime(2026, 10, 24, 5, 29, tzinfo=JST))
        self.assertEqual(result["status"], "not_due")

    def test_actions_are_idempotent_and_progress_is_sqlite_backed(self) -> None:
        day = "2026-10-24"
        self.assertEqual(start_gate(self.db, now=datetime(2026, 10, 24, 5, 30, tzinfo=JST))["status"], "allowed")
        record_action(self.db, routine_date=day, candidate_id="room-a", room_url="https://room.rakuten.co.jp/room-a/items", followed=True, liked=False, follow_status="followed", like_status="goal_reached")
        record_action(self.db, routine_date=day, candidate_id="room-a", room_url="https://room.rakuten.co.jp/room-a/items", followed=True, liked=True, follow_status="already_following", like_status="liked")
        self.assertEqual(progress(self.db, day), {"room-a": {"followed": True, "liked": True}})

    def test_interrupted_run_can_resume_without_losing_progress(self) -> None:
        day = "2026-10-24"
        self.assertEqual(start_gate(self.db, now=datetime(2026, 10, 24, 5, 30, tzinfo=JST))["status"], "allowed")
        record_action(self.db, routine_date=day, candidate_id="room-a", room_url="https://room.rakuten.co.jp/room-a/items",
                      followed=True, liked=False, follow_status="followed", like_status="pending")
        resumed = start_gate(self.db, now=datetime(2026, 10, 24, 5, 45, tzinfo=JST))
        self.assertEqual(resumed["status"], "allowed")
        self.assertEqual(progress(self.db, day)["room-a"]["followed"], True)

    def test_heartbeat_is_sanitized(self) -> None:
        path = Path(self.temp.name) / "heartbeat.json"
        payload = sanitized_heartbeat(path, role="engagement", status="completed", routine_date="2026-10-24")
        self.assertEqual(set(payload), {"role", "status", "routine_date", "updated_at"})
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("url", json.dumps(raw).lower())

    def test_external_audit_report_is_sanitized(self) -> None:
        import room_external_audit
        from room_external_audit import audit as external_audit

        day = "2026-10-24"
        start_gate(self.db, now=datetime(2026, 10, 24, 7, 31, tzinfo=JST))
        now = datetime(2026, 10, 24, 12, 0, tzinfo=JST)
        for path, role in ((self.temp.name + "/orchestrator.json", "orchestrator"),
                           (self.temp.name + "/engagement.json", "engagement")):
            sanitized_heartbeat(Path(path), role=role, status="ok", routine_date=day, now=now)
        report_path = Path(self.temp.name) / "audit.json"
        with patch.object(room_external_audit, "DATABASE_PATH", self.db), \
             patch.object(room_external_audit, "LEDGER_PATH", Path(self.temp.name) / "ledger.jsonl"), \
             patch.object(room_external_audit, "REPORT_PATH", report_path), \
             patch.object(room_external_audit, "ORCHESTRATOR_HEARTBEAT", Path(self.temp.name) / "orchestrator.json"), \
             patch.object(room_external_audit, "ENGAGEMENT_HEARTBEAT", Path(self.temp.name) / "engagement.json"):
            result = external_audit(now=now, routine_date=day)
        raw = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("room.rakuten.co.jp", raw)
        self.assertNotIn("body", raw.lower())
        self.assertTrue(report_path.exists())

    def test_same_day_terminal_engagement_heartbeat_is_not_stale(self) -> None:
        from room_external_audit import _heartbeat_status

        path = Path(self.temp.name) / "engagement.json"
        sanitized_heartbeat(
            path,
            role="engagement",
            status="completed",
            routine_date="2026-10-24",
            now=datetime(2026, 10, 24, 5, 45, tzinfo=JST),
        )
        status, error = _heartbeat_status(
            path,
            now=datetime(2026, 10, 24, 20, 45, tzinfo=JST),
            max_age_minutes=180,
        )
        self.assertEqual((status, error), ("ok", ""))


if __name__ == "__main__":
    unittest.main()
