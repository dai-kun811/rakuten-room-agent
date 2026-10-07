from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from room_operation_contract import (
    ContractError,
    IncidentRoute,
    IncidentStatus,
    ReasonCode,
    SlotStatus,
    route_for_reason,
    validate_manifest_v2,
)
from room_orchestrator import (
    AuthenticationRequired,
    Confirmation,
    InjectedCrash,
    PreSubmitFailure,
    RoomOrchestrator,
)
from room_state_store import RoomStateStore, StateConflictError


NOW = datetime(2026, 10, 7, 3, 0, tzinfo=timezone.utc)


def candidate() -> dict:
    body = "商品ページで確認した内容をもとにした投稿文です。"
    return {
        "product_url": "https://item.rakuten.co.jp/shop/item-1",
        "normalized_url": "https://item.rakuten.co.jp/shop/item-1",
        "product_name": "商品",
        "product_type": "test",
        "body": body,
        "hashtags": ["#育児"],
        "content_hash": hashlib.sha256(
            json.dumps(
                {
                    "body": body,
                    "hashtags": ["#育児"],
                    "normalized_url": "https://item.rakuten.co.jp/shop/item-1",
                    "title": "",
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
        "quality": {"status": "passed", "errors": []},
    }


class Gateway:
    def __init__(self, *, result: Confirmation = Confirmation.PRESENT, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    def submit(self, value, *, before_submit, after_submit):
        self.calls += 1
        if self.error is not None:
            raise self.error
        before_submit()
        after_submit()
        return self.result


class RoomOrchestratorFaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "operations.db"
        self.ledger = Path(self.temp.name) / "post-ledger.jsonl"
        self.store = RoomStateStore(self.db)
        self.store.initialize()
        value = candidate()
        self.store.create_slot(
            "2026-10-07", "noon", status=SlotStatus.READY,
            manifest_revision=1, normalized_url=value["normalized_url"],
            content_hash=value["content_hash"], product_type="test", now=NOW,
        )

    def test_success_posts_once_and_syncs_legacy_ledger(self) -> None:
        gateway = Gateway()
        orchestrator = RoomOrchestrator(self.store, gateway, legacy_ledger_path=self.ledger)
        result = orchestrator.execute_slot("2026-10-07", "noon", candidate(), now=NOW)
        again = orchestrator.execute_slot("2026-10-07", "noon", candidate(), now=NOW)
        self.assertEqual(result.status, SlotStatus.POSTED)
        self.assertEqual(again.status, SlotStatus.POSTED)
        self.assertEqual(gateway.calls, 1)
        self.assertEqual(len(self.ledger.read_text(encoding="utf-8").splitlines()), 1)

    def test_crash_after_claim_does_not_issue_second_submit(self) -> None:
        gateway = Gateway()
        orchestrator = RoomOrchestrator(
            self.store, gateway,
            failure_hook=lambda stage: (_ for _ in ()).throw(InjectedCrash()) if stage == "after_claim" else None,
        )
        with self.assertRaises(InjectedCrash):
            orchestrator.execute_slot("2026-10-07", "noon", candidate(), now=NOW)
        self.assertEqual(self.store.get_slot("2026-10-07", "noon").status, SlotStatus.CLAIMED)
        with self.assertRaises(StateConflictError):
            RoomOrchestrator(self.store, gateway).execute_slot("2026-10-07", "noon", candidate(), now=NOW)
        self.assertEqual(gateway.calls, 0)

    def test_crash_after_submitting_never_auto_reposts(self) -> None:
        gateway = Gateway()
        orchestrator = RoomOrchestrator(
            self.store, gateway,
            failure_hook=lambda stage: (_ for _ in ()).throw(InjectedCrash()) if stage == "after_submitting_commit" else None,
        )
        with self.assertRaises(InjectedCrash):
            orchestrator.execute_slot("2026-10-07", "noon", candidate(), now=NOW)
        self.assertEqual(self.store.get_slot("2026-10-07", "noon").status, SlotStatus.SUBMITTING)
        outcome = RoomOrchestrator(self.store, gateway).execute_slot("2026-10-07", "noon", candidate(), now=NOW)
        self.assertEqual(outcome.status, SlotStatus.UNCERTAIN)
        self.assertEqual(self.store.get_incident(outcome.incident_id)["status"], IncidentStatus.NEEDS_HUMAN.value)
        self.assertEqual(gateway.calls, 1)

    def test_after_2230_expires_without_gateway_call(self) -> None:
        gateway = Gateway()
        late = datetime(2026, 10, 7, 13, 31, tzinfo=timezone.utc)
        outcome = RoomOrchestrator(self.store, gateway).execute_slot(
            "2026-10-07", "noon", candidate(), now=late
        )
        self.assertEqual(outcome.status, SlotStatus.EXPIRED_UNPOSTED)
        self.assertEqual(gateway.calls, 0)

    def test_crash_before_posted_commit_never_auto_reposts(self) -> None:
        gateway = Gateway()
        orchestrator = RoomOrchestrator(
            self.store, gateway,
            failure_hook=lambda stage: (_ for _ in ()).throw(InjectedCrash()) if stage == "before_posted_commit" else None,
        )
        with self.assertRaises(InjectedCrash):
            orchestrator.execute_slot("2026-10-07", "noon", candidate(), now=NOW)
        self.assertEqual(self.store.get_slot("2026-10-07", "noon").status, SlotStatus.SUBMITTED_UNCONFIRMED)
        RoomOrchestrator(self.store, gateway).execute_slot("2026-10-07", "noon", candidate(), now=NOW)
        self.assertEqual(gateway.calls, 1)

    def test_crash_after_posted_commit_is_terminal(self) -> None:
        gateway = Gateway()
        orchestrator = RoomOrchestrator(
            self.store, gateway, legacy_ledger_path=self.ledger,
            failure_hook=lambda stage: (_ for _ in ()).throw(InjectedCrash()) if stage == "after_posted_commit" else None,
        )
        with self.assertRaises(InjectedCrash):
            orchestrator.execute_slot("2026-10-07", "noon", candidate(), now=NOW)
        self.assertEqual(self.store.get_slot("2026-10-07", "noon").status, SlotStatus.POSTED)
        self.assertFalse(self.ledger.exists())
        RoomOrchestrator(self.store, gateway, legacy_ledger_path=self.ledger).execute_slot(
            "2026-10-07", "noon", candidate(), now=NOW
        )
        self.assertEqual(gateway.calls, 1)
        self.assertEqual(len(self.ledger.read_text(encoding="utf-8").splitlines()), 1)

    def test_pre_submit_failure_is_retryable_but_auth_is_human_only(self) -> None:
        gateway = Gateway(error=PreSubmitFailure("page timeout"))
        result = RoomOrchestrator(self.store, gateway).execute_slot("2026-10-07", "noon", candidate(), now=NOW)
        self.assertEqual(result.status, SlotStatus.FAILED_PRE_SUBMIT)
        self.assertEqual(self.store.get_incident(result.incident_id)["status"], IncidentStatus.AUTO_RECOVERING.value)

        other = candidate()
        other["normalized_url"] = other["product_url"] = "https://item.rakuten.co.jp/shop/item-2"
        other["content_hash"] = "b" * 64
        self.store.create_slot("2026-10-07", "evening", status=SlotStatus.READY,
                               manifest_revision=1, normalized_url=other["normalized_url"],
                               content_hash=other["content_hash"], now=NOW)
        auth = RoomOrchestrator(self.store, Gateway(error=AuthenticationRequired("login"))).execute_slot(
            "2026-10-07", "evening", other, now=NOW
        )
        self.assertEqual(self.store.get_incident(auth.incident_id)["status"], IncidentStatus.NEEDS_HUMAN.value)

    def test_unknown_confirmation_becomes_uncertain(self) -> None:
        result = RoomOrchestrator(self.store, Gateway(result=Confirmation.UNKNOWN)).execute_slot(
            "2026-10-07", "noon", candidate(), now=NOW
        )
        self.assertEqual(result.status, SlotStatus.UNCERTAIN)
        self.assertEqual(self.store.get_slot("2026-10-07", "noon").status, SlotStatus.UNCERTAIN)

    def test_state_survives_process_restart(self) -> None:
        RoomOrchestrator(self.store, Gateway()).execute_slot("2026-10-07", "noon", candidate(), now=NOW)
        reopened = RoomStateStore(self.db)
        reopened.initialize()
        self.assertEqual(reopened.get_slot("2026-10-07", "noon").status, SlotStatus.POSTED)

    def test_disk_full_during_claim_rolls_back_slot_and_attempt(self) -> None:
        original = self.store._append_event

        def fail_event(*args, **kwargs):
            raise sqlite3.OperationalError("database or disk is full")

        self.store._append_event = fail_event
        with self.assertRaises(sqlite3.OperationalError):
            RoomOrchestrator(self.store, Gateway()).execute_slot(
                "2026-10-07", "noon", candidate(), now=NOW
            )
        self.store._append_event = original
        self.assertEqual(self.store.get_slot("2026-10-07", "noon").status, SlotStatus.READY)
        self.assertIsNone(self.store.get_active_post_attempt("2026-10-07", "noon"))

    def test_two_simultaneous_triggers_submit_once(self) -> None:
        gateway = Gateway()

        def run_once():
            try:
                return RoomOrchestrator(self.store, gateway).execute_slot(
                    "2026-10-07", "noon", candidate(), now=NOW
                ).status
            except StateConflictError:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _value: run_once(), range(2)))
        self.assertEqual(gateway.calls, 1)
        self.assertIn(SlotStatus.POSTED, statuses)

    def test_corrupt_manifest_is_rejected_before_state_change(self) -> None:
        with self.assertRaises(ContractError):
            validate_manifest_v2({"schema_version": 2, "slots": {}})
        self.assertEqual(self.store.get_slot("2026-10-07", "noon").status, SlotStatus.READY)

    def test_corrupt_database_cannot_be_initialized(self) -> None:
        corrupt = Path(self.temp.name) / "corrupt.db"
        corrupt.write_bytes(b"not-a-sqlite-database")
        with self.assertRaises(sqlite3.DatabaseError):
            RoomStateStore(corrupt).initialize()

    def test_external_faults_route_without_posting(self) -> None:
        self.assertEqual(route_for_reason(ReasonCode.TRANSIENT_NETWORK), IncidentRoute.AUTO_RETRY)
        self.assertEqual(route_for_reason(ReasonCode.ACTIONS_RUN_DELAYED), IncidentRoute.AUTO_RETRY)
        self.assertEqual(route_for_reason(ReasonCode.PROFILE_LOCK_BUSY), IncidentRoute.AUTO_RETRY)
        self.assertEqual(route_for_reason(ReasonCode.GIT_DIVERGED), IncidentRoute.USER_ACTION_REQUIRED)
        self.assertEqual(route_for_reason(ReasonCode.DATABASE_CORRUPT), IncidentRoute.OPERATIONS_REQUIRED)


if __name__ == "__main__":
    unittest.main()
