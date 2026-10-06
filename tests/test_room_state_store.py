from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from room_operation_contract import (
    IncidentStatus,
    ReasonCode,
    SlotStatus,
    TransitionError,
)
from room_state_store import (
    RoomStateStore,
    StateConflictError,
)


NOW = datetime(2026, 10, 6, 7, 0, tzinfo=timezone.utc)


class RoomStateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "operations.db"
        self.store = RoomStateStore(self.path)
        self.store.initialize()

    def test_database_uses_wal_full_sync_and_passes_quick_check(self) -> None:
        with closing(self.store.connect()) as connection:
            journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
            synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]
        self.assertEqual(str(journal).lower(), "wal")
        self.assertEqual(synchronous, 2)
        self.store.quick_check()

    def test_slot_transition_uses_compare_and_swap(self) -> None:
        original = self.store.create_slot("2026-10-06", "morning", now=NOW)
        ready = self.store.transition_slot(
            "2026-10-06",
            "morning",
            expected_status=SlotStatus.PENDING,
            expected_version=original.version,
            target_status=SlotStatus.READY,
            now=NOW,
        )
        self.assertEqual(ready.status, SlotStatus.READY)
        self.assertEqual(ready.version, 1)
        with self.assertRaises(StateConflictError):
            self.store.transition_slot(
                "2026-10-06",
                "morning",
                expected_status=SlotStatus.PENDING,
                expected_version=original.version,
                target_status=SlotStatus.BLOCKED,
                now=NOW,
            )

    def test_posted_slot_is_terminal(self) -> None:
        self.store.create_slot(
            "2026-10-06",
            "morning",
            status=SlotStatus.POSTED,
            normalized_url="https://item.rakuten.co.jp/shop/item-1",
            now=NOW,
        )
        record = self.store.get_slot("2026-10-06", "morning")
        assert record is not None
        with self.assertRaises(TransitionError):
            self.store.transition_slot(
                "2026-10-06",
                "morning",
                expected_status=record.status,
                expected_version=record.version,
                target_status=SlotStatus.READY,
                now=NOW,
            )

    def test_posted_product_url_is_unique_across_days(self) -> None:
        self.store.create_slot(
            "2026-10-06",
            "morning",
            status=SlotStatus.POSTED,
            normalized_url="https://item.rakuten.co.jp/shop/item-1",
            now=NOW,
        )
        with self.assertRaises(StateConflictError):
            self.store.create_slot(
                "2026-10-07",
                "noon",
                status=SlotStatus.POSTED,
                normalized_url="https://item.rakuten.co.jp/shop/item-1?x=1",
                now=NOW,
            )

    def test_uncertain_cannot_be_cleared_without_manual_resolution(self) -> None:
        record = self.store.create_slot(
            "2026-10-06", "morning", status=SlotStatus.UNCERTAIN, now=NOW
        )
        with self.assertRaises(TransitionError):
            self.store.transition_slot(
                "2026-10-06",
                "morning",
                expected_status=SlotStatus.UNCERTAIN,
                expected_version=record.version,
                target_status=SlotStatus.FAILED_PRE_SUBMIT,
                now=NOW,
            )
        cleared = self.store.transition_slot(
            "2026-10-06",
            "morning",
            expected_status=SlotStatus.UNCERTAIN,
            expected_version=record.version,
            target_status=SlotStatus.FAILED_PRE_SUBMIT,
            manual_resolution=True,
            now=NOW,
        )
        self.assertEqual(cleared.status, SlotStatus.FAILED_PRE_SUBMIT)

    def test_slot_state_and_event_are_committed_together(self) -> None:
        self.store.create_slot("2026-10-06", "morning", now=NOW)
        with closing(self.store.connect()) as connection:
            slot_count = connection.execute("SELECT COUNT(*) FROM slots").fetchone()[0]
            event_count = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertEqual(slot_count, 1)
        self.assertEqual(event_count, 1)

    def test_create_incident_deduplicates_open_fingerprint(self) -> None:
        first = self.store.create_incident(
            "2026-10-06",
            reason=ReasonCode.QUALITY_POOL_EXHAUSTED,
            slot="noon",
            component="generator",
            last_safe_state="morning_posted",
            next_action="codex diagnosis",
            now=NOW,
        )
        second = self.store.create_incident(
            "2026-10-06",
            reason=ReasonCode.QUALITY_POOL_EXHAUSTED,
            slot="noon",
            component="generator",
            last_safe_state="morning_posted",
            next_action="codex diagnosis",
            now=NOW,
        )
        self.assertEqual(first, second)
        self.store.transition_incident(
            first,
            expected_status=IncidentStatus.OPEN,
            target_status=IncidentStatus.CODEX_QUEUED,
            next_action="wait for bounded codex recovery",
            now=NOW,
        )

    def test_legacy_import_is_idempotent_and_uses_latest_slot_event(self) -> None:
        ledger = Path(self.temp_dir.name) / "post-ledger.jsonl"
        rows = [
            {
                "post_slot": "2026-10-06:morning",
                "status": "reserved",
                "normalized_url": "https://item.rakuten.co.jp/shop/morning",
            },
            {
                "post_slot": "2026-10-06:morning",
                "status": "posted",
                "normalized_url": "https://item.rakuten.co.jp/shop/morning",
                "product_type": "wipes",
            },
            {
                "post_slot": "2026-10-06:noon",
                "status": "reserved",
                "normalized_url": "https://item.rakuten.co.jp/shop/noon",
            },
        ]
        ledger.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n{broken",
            encoding="utf-8",
        )
        first = self.store.import_legacy_ledger(ledger, now=NOW)
        second = self.store.import_legacy_ledger(ledger, now=NOW)
        self.assertEqual(first.imported_lines, 3)
        self.assertEqual(first.malformed_lines, 1)
        self.assertEqual(second.imported_lines, 0)
        self.assertEqual(second.duplicate_lines, 3)
        self.assertEqual(second.malformed_lines, 1)
        morning = self.store.get_slot("2026-10-06", "morning")
        noon = self.store.get_slot("2026-10-06", "noon")
        assert morning is not None and noon is not None
        self.assertEqual(morning.status, SlotStatus.POSTED)
        self.assertEqual(noon.status, SlotStatus.UNCERTAIN)

    def test_legacy_missing_button_failure_is_pre_submit(self) -> None:
        ledger = Path(self.temp_dir.name) / "post-ledger.jsonl"
        ledger.write_text(
            json.dumps(
                {
                    "post_slot": "2026-10-06:evening",
                    "status": "failed",
                    "detail": "楽天商品ページにROOM投稿ボタンが見つかりません。",
                    "normalized_url": "https://item.rakuten.co.jp/shop/evening",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.store.import_legacy_ledger(ledger, now=NOW)
        evening = self.store.get_slot("2026-10-06", "evening")
        assert evening is not None
        self.assertEqual(evening.status, SlotStatus.FAILED_PRE_SUBMIT)

    def test_backup_is_consistent_and_readable(self) -> None:
        self.store.create_slot("2026-10-06", "morning", now=NOW)
        backup_path = Path(self.temp_dir.name) / "backup" / "operations.db"
        self.store.backup_to(backup_path)
        backup = RoomStateStore(backup_path)
        backup.quick_check()
        record = backup.get_slot("2026-10-06", "morning")
        assert record is not None
        self.assertEqual(record.status, SlotStatus.PENDING)

    def test_snapshot_contains_operational_fields_but_no_secret_material(self) -> None:
        self.store.create_slot("2026-10-06", "morning", now=NOW)
        snapshot = self.store.export_snapshot("2026-10-06")
        encoded = json.dumps(snapshot, ensure_ascii=False).lower()
        self.assertEqual(snapshot["slots"]["morning"]["status"], "pending")
        for forbidden in ("authorization", "cookie", "password", "api_key"):
            self.assertNotIn(forbidden, encoded)

    def test_unknown_database_schema_version_is_rejected(self) -> None:
        with closing(self.store.connect()) as connection:
            connection.execute(
                "UPDATE metadata SET value = '999' WHERE key = 'schema_version'"
            )
        with self.assertRaises(Exception):
            self.store.initialize()


if __name__ == "__main__":
    unittest.main()
