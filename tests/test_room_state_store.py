from __future__ import annotations

import json
import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from room_operation_contract import (
    IncidentStatus,
    POST_SLOTS,
    ReasonCode,
    SlotStatus,
    TransitionError,
)
from room_state_store import (
    DEFAULT_RETRY_BUDGET,
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

    def test_recovery_control_consumes_budget_once_and_checks_fence(self) -> None:
        self.store.reserve_recovery_control(
            recovery_id="recovery-20261007-01",
            routine_date="2026-10-07",
            old_run_id="37579267266",
            expected_head_sha="a" * 40,
            revision=297,
            owner_id="owner-a",
            now=NOW,
        )
        with self.assertRaises(StateConflictError):
            self.store.reserve_recovery_control(
                recovery_id="recovery-20261007-01",
                routine_date="2026-10-07",
                old_run_id="37579267266",
                expected_head_sha="a" * 40,
                revision=297,
                owner_id="owner-b",
                now=NOW,
            )
        self.store.complete_recovery_control("recovery-20261007-01", owner_id="owner-a", replacement_run_id="37625981589", now=NOW)
        control = self.store.assert_recovery_control(
            "recovery-20261007-01", expected_head_sha="a" * 40, revision=297
        )
        self.assertEqual(control["status"], "ready")
        self.assertEqual(control["replacement_run_id"], "37625981589")

    def test_preflight_recovery_can_rebind_head_without_new_budget(self) -> None:
        self.store.reserve_recovery_control(
            recovery_id="recovery-20261007-02", routine_date="2026-10-07",
            old_run_id="37579267266", expected_head_sha="a" * 40,
            revision=298, owner_id="owner-a", now=NOW,
        )
        self.store.rebind_preflight_recovery_control(
            "recovery-20261007-02", owner_id="owner-a", expected_head_sha="b" * 40, now=NOW
        )
        value = self.store.assert_recovery_control(
            "recovery-20261007-02", expected_head_sha="b" * 40, revision=298
        )
        self.assertEqual(value["budget_consumed"], 1)

    def test_pre_submit_budget_reclaim_requires_no_submit_started(self) -> None:
        incident = self.store.create_incident("2026-10-06", reason=ReasonCode.FAILED_PRE_SUBMIT, slot="morning", component="test", last_safe_state=SlotStatus.FAILED_PRE_SUBMIT.value, next_action="retry", now=NOW)
        self.store.transition_incident(incident, expected_status=IncidentStatus.OPEN, target_status=IncidentStatus.AUTO_RECOVERING, next_action="retry", now=NOW)
        self.store.consume_retry_budget(incident, budget_key="pre_submit_retries", now=NOW)
        self.store.reclaim_pre_submit_budget(incident, now=NOW)
        row = self.store.get_incident(incident)
        self.assertEqual(json.loads(row["attempts_json"]), {"pre_submit_retries": 0})

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

    def test_human_confirmed_uncertain_resolves_posted_without_repost(self) -> None:
        self.store.create_slot(
            "2026-10-07", "morning", status=SlotStatus.READY,
            normalized_url="https://item.rakuten.co.jp/example/item",
            content_hash="a" * 64, manifest_revision=297,
        )
        claimed = self.store.claim_post_attempt("2026-10-07", "morning", expected_version=0)
        self.store.advance_post_attempt(
            claimed.attempt_id, expected_slot_status=SlotStatus.CLAIMED,
            target_slot_status=SlotStatus.SUBMITTING,
            expected_attempt_status=SlotStatus.CLAIMED.value,
            target_attempt_status=SlotStatus.SUBMITTING.value, submit_started=True,
        )
        self.store.advance_post_attempt(
            claimed.attempt_id, expected_slot_status=SlotStatus.SUBMITTING,
            target_slot_status=SlotStatus.UNCERTAIN,
            expected_attempt_status=SlotStatus.SUBMITTING.value,
            target_attempt_status=SlotStatus.UNCERTAIN.value, submit_started=True,
        )
        incident = self.store.create_incident(
            "2026-10-07", reason=ReasonCode.POST_RESULT_UNCERTAIN,
            slot="morning", component="test", last_safe_state="uncertain",
            next_action="wait for human", manifest_revision=297,
        )
        self.store.transition_incident(
            incident, expected_status=IncidentStatus.OPEN,
            target_status=IncidentStatus.NEEDS_HUMAN, next_action="wait for human",
        )
        posted = self.store.resolve_uncertain_as_posted(
            "2026-10-07", "morning", evidence_source="authenticated_room",
            evidence_note="operator confirmed matching morning item; no repost",
        )
        self.assertEqual(posted.status, SlotStatus.POSTED)
        self.assertEqual(self.store.get_post_attempt(claimed.attempt_id)["status"], "posted")
        self.assertEqual(self.store.get_incident(incident)["status"], IncidentStatus.RESOLVED.value)

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
        other_day = self.store.create_incident(
            "2026-10-07",
            reason=ReasonCode.QUALITY_POOL_EXHAUSTED,
            slot="noon",
            component="generator",
            last_safe_state="morning_posted",
            next_action="codex diagnosis",
            now=NOW,
        )
        self.assertNotEqual(first, other_day)

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

    def test_legacy_six_slot_history_is_skipped_without_aborting_migration(self) -> None:
        ledger = Path(self.temp_dir.name) / "post-ledger.jsonl"
        ledger.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "post_slot": "2026-10-04:morning_1",
                            "status": "posted",
                            "normalized_url": "https://item.rakuten.co.jp/shop/old",
                        }
                    ),
                    json.dumps(
                        {
                            "post_slot": "2026-10-06:morning",
                            "status": "posted",
                            "normalized_url": "https://item.rakuten.co.jp/shop/current",
                        }
                    ),
                ]
            ),
            encoding="utf-8",
        )

        result = self.store.import_legacy_ledger(ledger, now=NOW)

        self.assertEqual(result.imported_lines, 1)
        self.assertEqual(result.malformed_lines, 1)
        self.assertIsNone(self.store.get_slot("2026-10-04", "morning"))
        self.assertEqual(
            self.store.get_slot("2026-10-06", "morning").status,
            SlotStatus.POSTED,
        )

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

    def test_retry_budget_is_atomic_and_transitions_to_budget_exhausted(self) -> None:
        incident = self.store.create_incident(
            "2026-10-06", reason=ReasonCode.TRANSIENT_NETWORK,
            component="worker", last_safe_state="ready", next_action="retry", now=NOW,
        )
        first = self.store.consume_retry_budget(incident, budget_key="transient_attempts", now=NOW)
        second = self.store.consume_retry_budget(incident, budget_key="transient_attempts", now=NOW)
        third = self.store.consume_retry_budget(incident, budget_key="transient_attempts", now=NOW)
        self.assertEqual((first.attempt, second.attempt), (1, 2))
        self.assertTrue(second.exhausted)
        self.assertTrue(third.exhausted)
        self.assertEqual(self.store.get_incident(incident)["status"], IncidentStatus.BUDGET_EXHAUSTED.value)

    def test_incident_lease_is_exclusive_and_expires(self) -> None:
        incident = self.store.create_incident(
            "2026-10-06", reason=ReasonCode.TRANSIENT_NETWORK,
            component="worker", last_safe_state="ready", next_action="retry", now=NOW,
        )
        self.assertTrue(self.store.acquire_incident_lease(incident, owner="a", ttl_seconds=60, now=NOW))
        self.assertFalse(self.store.acquire_incident_lease(incident, owner="b", ttl_seconds=60, now=NOW))
        later = NOW.replace(minute=2)
        self.assertTrue(self.store.acquire_incident_lease(incident, owner="b", ttl_seconds=60, now=later))
        with self.assertRaises(StateConflictError):
            self.store.transition_incident(
                incident, expected_status=IncidentStatus.OPEN,
                target_status=IncidentStatus.AUTO_RECOVERING, next_action="recover",
                lease_owner="a", now=later,
            )

    def test_incident_recovery_status_path_is_bounded(self) -> None:
        incident = self.store.create_incident(
            "2026-10-06", reason=ReasonCode.CLASSIFICATION_UNSUPPORTED,
            component="generator", last_safe_state="ready", next_action="recover", now=NOW,
        )
        self.store.transition_incident(incident, expected_status=IncidentStatus.OPEN,
                                       target_status=IncidentStatus.AUTO_RECOVERING,
                                       next_action="bounded retry", now=NOW)
        self.store.transition_incident(incident, expected_status=IncidentStatus.AUTO_RECOVERING,
                                       target_status=IncidentStatus.CODEX_QUEUED,
                                       next_action="codex disabled in Phase 5", now=NOW)
        self.store.transition_incident(incident, expected_status=IncidentStatus.CODEX_QUEUED,
                                       target_status=IncidentStatus.NEEDS_HUMAN,
                                       next_action="wait for Phase 6 authorization", now=NOW)
        self.assertEqual(self.store.get_incident(incident)["status"], IncidentStatus.NEEDS_HUMAN.value)

    def test_previous_day_expire_and_catch_up_hold_uncertain(self) -> None:
        self.store.create_slot("2026-10-05", "morning", status=SlotStatus.READY, now=NOW)
        self.store.create_slot("2026-10-05", "noon", status=SlotStatus.UNCERTAIN, now=NOW)
        result = self.store.expire_previous_days("2026-10-06", now=NOW)
        self.assertEqual(result["expired_slots"], 1)
        self.assertEqual(self.store.get_slot("2026-10-05", "morning").status, SlotStatus.EXPIRED_UNPOSTED)
        plan = self.store.build_catch_up_plan("2026-10-06")
        self.assertEqual(plan.expired_slots, ())
        self.assertEqual(plan.human_review_slots, ("2026-10-05:noon",))
        self.assertEqual(plan.missing_today_slots, POST_SLOTS)

    def test_manifest_revision_updates_only_safe_slots(self) -> None:
        def manifest(revision: int, suffix: str) -> dict:
            slots = {}
            for slot in POST_SLOTS:
                url = f"https://item.rakuten.co.jp/shop/{slot}-{suffix}"
                body = f"{slot} の確認済み商品情報です。"
                slots[slot] = {"status": "ready", "candidate": {
                    "product_url": url, "normalized_url": url, "product_name": slot,
                    "product_type": "test", "body": body,
                    "content_hash": hashlib.sha256(body.encode()).hexdigest(),
                    "quality": {"status": "passed", "errors": []},
                }}
            return {"schema_version": 2, "routine_date_jst": "2026-10-06", "revision": revision,
                    "actions_run_id": f"run-{revision}", "head_sha": "a" * 40,
                    "generated_at": "2026-10-06T07:00:00+09:00", "slots": slots}
        self.assertEqual(self.store.accept_manifest(manifest(1, "one"), now=NOW), 1)
        slot = self.store.get_slot("2026-10-06", "morning")
        self.store.transition_slot("2026-10-06", "morning", expected_status=slot.status,
                                   expected_version=slot.version, target_status=SlotStatus.CLAIMED, now=NOW)
        self.assertEqual(self.store.accept_manifest(manifest(2, "two"), now=NOW), 2)
        self.assertEqual(self.store.get_slot("2026-10-06", "morning").status, SlotStatus.CLAIMED)
        self.assertTrue(self.store.get_slot("2026-10-06", "noon").normalized_url.endswith("noon-two"))

    def test_legacy_sync_is_idempotent_and_importable(self) -> None:
        ledger = Path(self.temp_dir.name) / "compat-ledger.jsonl"
        self.store.create_slot("2026-10-06", "morning", status=SlotStatus.POSTED,
                               normalized_url="https://item.rakuten.co.jp/shop/morning", now=NOW)
        first = self.store.sync_legacy_ledger(ledger, routine_date="2026-10-06", now=NOW)
        second = self.store.sync_legacy_ledger(ledger, routine_date="2026-10-06", now=NOW)
        self.assertEqual(first.written_lines, 1)
        self.assertEqual(second.written_lines, 0)
        self.assertEqual(self.store.import_legacy_ledger(ledger, now=NOW).imported_lines, 1)


if __name__ == "__main__":
    unittest.main()
