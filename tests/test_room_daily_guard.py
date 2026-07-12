from __future__ import annotations

import json
import logging
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from room_daily_guard import (
    DailyGuardError,
    due_slot_labels,
    ensure_generation_ready,
    ensure_slot_posted,
    generation_recovery_already_dispatched,
    mark_generation_recovery_dispatched,
    read_latest_slot_events,
    report_has_all_slots,
    report_item_for_slot,
)


class RoomDailyGuardTest(unittest.TestCase):
    @staticmethod
    def ready_report() -> dict:
        return {
            "run_id": "report-1",
            "required_post_slots": ["morning", "noon", "evening"],
            "missing_post_slots": [],
            "items": [
                {"status": "ready", "post_slot": "morning", "product_url": "https://example.com/m"},
                {"status": "ready", "post_slot": "noon", "product_url": "https://example.com/n"},
                {"status": "ready", "post_slot": "evening", "product_url": "https://example.com/e"},
            ],
        }

    def test_report_requires_all_three_ready_slots(self) -> None:
        report = {
            "required_post_slots": ["morning", "noon", "evening"],
            "missing_post_slots": [],
            "items": [
                {"status": "ready", "post_slot": "morning"},
                {"status": "ready", "post_slot": "noon"},
                {"status": "ready", "post_slot": "evening"},
            ],
        }
        self.assertTrue(report_has_all_slots(report))
        report["missing_post_slots"] = ["evening"]
        self.assertFalse(report_has_all_slots(report))

    def test_due_slots_accumulate_for_same_day_catch_up(self) -> None:
        windows = [("morning", 8, 11), ("noon", 11, 16), ("evening", 17, 22)]
        self.assertEqual(
            due_slot_labels(datetime(2026, 7, 6, 8, 30), windows=windows),
            ["morning"],
        )
        self.assertEqual(
            due_slot_labels(datetime(2026, 7, 6, 18, 30), windows=windows),
            ["morning", "noon", "evening"],
        )

    def test_latest_slot_event_wins(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.jsonl"
            events = [
                {"post_slot": "2026-07-06:morning", "status": "failed"},
                {"post_slot": "2026-07-06:morning", "status": "posted"},
            ]
            path.write_text(
                "".join(json.dumps(event) + "\n" for event in events),
                encoding="utf-8",
            )
            self.assertEqual(
                read_latest_slot_events(path)["2026-07-06:morning"]["status"],
                "posted",
            )

    def test_report_item_must_be_ready_and_assigned(self) -> None:
        report = {
            "items": [
                {"status": "needs_review", "post_slot": "morning", "product_url": "x"},
                {"status": "ready", "post_slot": "morning", "product_url": "y"},
            ]
        }
        self.assertEqual(report_item_for_slot(report, "morning")["product_url"], "y")

    @patch("room_daily_guard.time.sleep", return_value=None)
    @patch("room_daily_guard.fetch_latest_generation_report")
    @patch("room_daily_guard.fetch_workflow_runs")
    def test_missing_generation_dispatches_once_then_accepts_three_slots(
        self,
        fetch_runs,
        fetch_report,
        _sleep,
    ) -> None:
        now = datetime(2026, 7, 6, 7, 30, tzinfo=timezone(timedelta(hours=9)))
        successful = {
            "id": 64,
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-07-05T22:05:00Z",
        }
        fetch_runs.side_effect = [[], [successful]]
        fetch_report.return_value = (successful, self.ready_report())
        response = Mock()
        response.raise_for_status.return_value = None
        session = Mock()
        session.post.return_value = response

        run, report = ensure_generation_ready(
            session,
            headers={"Authorization": "masked"},
            now=now,
            poll_seconds=0,
        )

        self.assertEqual(run["id"], 64)
        self.assertTrue(report_has_all_slots(report))
        session.post.assert_called_once()

    @patch("room_daily_guard.time.sleep", return_value=None)
    @patch("room_daily_guard.fetch_latest_generation_report")
    @patch("room_daily_guard.fetch_workflow_runs")
    def test_failed_generation_dispatches_one_recovery(
        self,
        fetch_runs,
        fetch_report,
        _sleep,
    ) -> None:
        now = datetime(2026, 7, 6, 7, 30, tzinfo=timezone(timedelta(hours=9)))
        failed = {
            "id": 63,
            "status": "completed",
            "conclusion": "failure",
            "created_at": "2026-07-05T22:05:00Z",
        }
        successful = {
            "id": 64,
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-07-05T22:08:00Z",
        }
        fetch_runs.side_effect = [[failed], [successful]]
        fetch_report.return_value = (successful, self.ready_report())
        response = Mock()
        response.raise_for_status.return_value = None
        session = Mock()
        session.post.return_value = response

        with TemporaryDirectory() as directory:
            recovery_state = Path(directory) / "recovery.json"
            run, report = ensure_generation_ready(
                session,
                headers={},
                now=now,
                poll_seconds=0,
                recovery_state_path=recovery_state,
            )

            self.assertEqual(run["id"], 64)
            self.assertTrue(report_has_all_slots(report))
            self.assertTrue(generation_recovery_already_dispatched("2026-07-06", path=recovery_state))
        session.post.assert_called_once()

    @patch("room_daily_guard.fetch_workflow_runs")
    def test_failed_generation_is_not_repeated_after_daily_recovery(self, fetch_runs) -> None:
        now = datetime(2026, 7, 6, 7, 30, tzinfo=timezone(timedelta(hours=9)))
        fetch_runs.return_value = [
            {
                "id": 63,
                "status": "completed",
                "conclusion": "failure",
                "created_at": "2026-07-05T22:05:00Z",
            }
        ]
        session = Mock()

        with TemporaryDirectory() as directory:
            recovery_state = Path(directory) / "recovery.json"
            mark_generation_recovery_dispatched("2026-07-06", 62, now=now, path=recovery_state)
            with self.assertRaises(DailyGuardError):
                ensure_generation_ready(
                    session,
                    headers={},
                    now=now,
                    recovery_state_path=recovery_state,
                )

        session.post.assert_not_called()

    @patch("room_daily_guard.run_post_worker", return_value=0)
    @patch("room_daily_guard.run_no_post_probe", return_value=True)
    @patch("room_daily_guard.read_latest_slot_events")
    def test_timeout_retry_requires_probe_and_confirms_posted(
        self,
        read_events,
        probe,
        run_worker,
    ) -> None:
        slot = "2026-07-06:morning"
        read_events.side_effect = [
            {slot: {"status": "failed", "detail": "TimeoutError"}},
            {slot: {"status": "posted"}},
        ]

        ensure_slot_posted(
            self.ready_report(),
            "morning",
            now=datetime(2026, 7, 6, 8, 30),
            logger=logging.getLogger("test"),
        )

        probe.assert_called_once_with("https://example.com/m")
        run_worker.assert_called_once_with("morning", retry_detail="TimeoutError")


if __name__ == "__main__":
    unittest.main()
