from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from room_operation_contract import IncidentStatus, POST_SLOTS, ReasonCode, SlotStatus
from room_orchestrator_worker import execute
from room_phase8_runner import (
    FencedArtifact,
    GenerationUnavailable,
    GenerationFence,
    audit_day,
    dispatch_generation_once,
    fetch_fenced_artifact,
    generation_fence,
    next_eligible_slot,
    record_generation_incidents,
    run as run_phase8,
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


class ActionsRunSession(Session):
    def __init__(self, *, status: str, conclusion: str | None) -> None:
        super().__init__()
        self.status = status
        self.conclusion = conclusion

    def get(self, url, **kwargs):
        if "/actions/runs/" in url:
            return Response(
                {
                    "id": 123,
                    "head_sha": "a" * 40,
                    "status": self.status,
                    "conclusion": self.conclusion,
                }
            )
        return super().get(url, **kwargs)


class OfflineSession(Session):
    def get(self, url, **kwargs):
        raise AssertionError(f"network access was not expected: {url}")


class WorkflowRunWithoutArtifactSession(Session):
    def __init__(
        self,
        *,
        status: str,
        conclusion: str | None,
        updated_at: str = "2026-10-09T03:05:00Z",
    ) -> None:
        super().__init__()
        self.status = status
        self.conclusion = conclusion
        self.updated_at = updated_at

    def get(self, url, **kwargs):
        if url.endswith("/actions/workflows/room-fenced-recovery.yml/runs"):
            return Response({"workflow_runs": [{
                "id": 123,
                "head_sha": "a" * 40,
                "status": self.status,
                "conclusion": self.conclusion,
                "event": "workflow_dispatch",
                "created_at": "2026-10-09T03:05:00Z",
                "updated_at": self.updated_at,
            }]})
        if url.endswith("/actions/runs/123/artifacts"):
            return Response({"artifacts": []})
        return super().get(url, **kwargs)


def phase8_manifest() -> dict:
    value = manifest()
    value["routine_date_jst"] = "2026-10-09"
    value["revision"] = 300
    value["recovery_id"] = "production-20261009"
    value["generation_channel"] = "local_fenced_recovery"
    return value


class RoomPhase8RunnerTests(unittest.TestCase):
    def _local_artifact(self, root: Path) -> tuple[RoomStateStore, GenerationFence]:
        store = RoomStateStore(root / "operations.db")
        store.reserve_recovery_control(
            recovery_id="production-20261009",
            routine_date="2026-10-09",
            old_run_id="37579267266",
            expected_head_sha="a" * 40,
            revision=300,
            owner_id="phase8-2026-10-09",
            now=NOW,
        )
        fence = GenerationFence(
            "production-20261009",
            "2026-10-09",
            "a" * 40,
            300,
            "phase8-2026-10-09",
            root / "recovery",
        )
        fence.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        fence.manifest_path.write_text(
            json.dumps(phase8_manifest(), ensure_ascii=False),
            encoding="utf-8",
        )
        return store, fence

    def test_failed_actions_partial_artifact_is_not_marked_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, fence = self._local_artifact(Path(temp))
            result = fetch_fenced_artifact(
                ActionsRunSession(status="completed", conclusion="failure"),
                headers={},
                store=store,
                fence=fence,
                now=NOW,
            )
            self.assertIsNotNone(result)
            self.assertEqual(result.actions_conclusion, "failure")
            control = store.get_recovery_control(fence.recovery_id)
            self.assertEqual(control["status"], "partial")
            self.assertEqual(control["replacement_run_id"], "123")

    def test_in_progress_actions_run_is_not_evaluated_as_an_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, fence = self._local_artifact(Path(temp))
            result = fetch_fenced_artifact(
                ActionsRunSession(status="in_progress", conclusion=None),
                headers={},
                store=store,
                fence=fence,
                now=NOW,
            )
            self.assertIsInstance(result, GenerationUnavailable)
            self.assertFalse(result.terminal)
            self.assertEqual(result.reason, ReasonCode.ACTIONS_RUN_DELAYED)
            self.assertEqual(store.get_recovery_control(fence.recovery_id)["status"], "running")

    def test_verified_local_manifest_survives_github_outage_and_rejects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, fence = self._local_artifact(Path(temp))
            first = fetch_fenced_artifact(
                ActionsRunSession(status="completed", conclusion="success"),
                headers={}, store=store, fence=fence, now=NOW,
            )
            self.assertIsInstance(first, FencedArtifact)
            self.assertTrue(fence.verification_path.exists())
            cached = fetch_fenced_artifact(
                OfflineSession(), headers={}, store=store, fence=fence, now=NOW,
            )
            self.assertEqual(cached, first)

            payload = json.loads(fence.manifest_path.read_text(encoding="utf-8"))
            payload["tampered"] = True
            fence.manifest_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "integrity"):
                fetch_fenced_artifact(
                    OfflineSession(), headers={}, store=store, fence=fence, now=NOW,
                )

    def test_verified_local_manifest_expires_at_posting_cutoff(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, fence = self._local_artifact(Path(temp))
            fetch_fenced_artifact(
                ActionsRunSession(status="completed", conclusion="success"),
                headers={}, store=store, fence=fence, now=NOW,
            )
            before_cutoff = datetime(2026, 10, 9, 13, 29, 59, tzinfo=timezone.utc)
            cached = fetch_fenced_artifact(
                OfflineSession(), headers={}, store=store, fence=fence, now=before_cutoff,
            )
            self.assertIsInstance(cached, FencedArtifact)
            expired = datetime(2026, 10, 9, 13, 30, tzinfo=timezone.utc)
            with self.assertRaisesRegex(RuntimeError, "expired"):
                fetch_fenced_artifact(
                    OfflineSession(), headers={}, store=store, fence=fence, now=expired,
                )

    def test_verified_local_manifest_rejects_tampered_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, fence = self._local_artifact(Path(temp))
            fetch_fenced_artifact(
                ActionsRunSession(status="completed", conclusion="success"),
                headers={}, store=store, fence=fence, now=NOW,
            )
            verification = json.loads(fence.verification_path.read_text(encoding="utf-8"))
            verification["valid_until"] = "2026-10-10T22:30:00+09:00"
            fence.verification_path.write_text(
                json.dumps(verification, ensure_ascii=False), encoding="utf-8"
            )
            with self.assertRaisesRegex(RuntimeError, "expiry"):
                fetch_fenced_artifact(
                    OfflineSession(), headers={}, store=store, fence=fence, now=NOW,
                )

    def test_unverified_manifest_with_mismatched_candidate_hash_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, fence = self._local_artifact(Path(temp))
            payload = json.loads(fence.manifest_path.read_text(encoding="utf-8"))
            payload["slots"]["morning"]["candidate"]["body"] += "tampered"
            fence.manifest_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "content hash mismatch"):
                fetch_fenced_artifact(
                    ActionsRunSession(status="completed", conclusion="success"),
                    headers={}, store=store, fence=fence, now=NOW,
                )

    def test_run_states_distinguish_in_progress_failed_and_missing_artifact(self) -> None:
        for status, conclusion, expected_reason, terminal in (
            ("in_progress", None, ReasonCode.ACTIONS_RUN_DELAYED, False),
            ("completed", "success", ReasonCode.ARTIFACT_DELAYED, False),
            ("completed", "failure", ReasonCode.ACTIONS_RUN_FAILED, True),
        ):
            with self.subTest(status=status, conclusion=conclusion), tempfile.TemporaryDirectory() as temp:
                store, fence = self._local_artifact(Path(temp))
                fence.manifest_path.unlink()
                result = fetch_fenced_artifact(
                    WorkflowRunWithoutArtifactSession(status=status, conclusion=conclusion),
                    headers={}, store=store, fence=fence, now=NOW,
                )
                self.assertIsInstance(result, GenerationUnavailable)
                self.assertEqual(result.reason, expected_reason)
                self.assertEqual(result.terminal, terminal)

    def test_successful_run_without_artifact_has_bounded_grace_period(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, fence = self._local_artifact(Path(temp))
            fence.manifest_path.unlink()
            session = WorkflowRunWithoutArtifactSession(
                status="completed", conclusion="success"
            )
            before_deadline = fetch_fenced_artifact(
                session,
                headers={},
                store=store,
                fence=fence,
                now=NOW + timedelta(minutes=14, seconds=59),
            )
            self.assertIsInstance(before_deadline, GenerationUnavailable)
            self.assertEqual(before_deadline.reason, ReasonCode.ARTIFACT_DELAYED)
            self.assertFalse(before_deadline.terminal)

            at_deadline = fetch_fenced_artifact(
                session,
                headers={},
                store=store,
                fence=fence,
                now=NOW + timedelta(minutes=15),
            )
            self.assertIsInstance(at_deadline, GenerationUnavailable)
            self.assertEqual(at_deadline.reason, ReasonCode.ARTIFACT_DELAYED)
            self.assertTrue(at_deadline.terminal)

    def test_successful_run_without_terminal_timestamp_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, fence = self._local_artifact(Path(temp))
            fence.manifest_path.unlink()
            result = fetch_fenced_artifact(
                WorkflowRunWithoutArtifactSession(
                    status="completed", conclusion="success", updated_at=""
                ),
                headers={}, store=store, fence=fence, now=NOW,
            )
            self.assertIsInstance(result, GenerationUnavailable)
            self.assertEqual(result.reason, ReasonCode.ARTIFACT_DELAYED)
            self.assertTrue(result.terminal)

    def test_expired_artifact_grace_records_one_incident_and_failed_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            heartbeat = root / "orchestrator-heartbeat.json"
            store = RoomStateStore(root / "operations.db")
            store.reserve_recovery_control(
                recovery_id="production-20261009", routine_date="2026-10-09",
                old_run_id="37579267266", expected_head_sha="a" * 40,
                revision=300, owner_id="phase8-2026-10-09", now=NOW,
            )
            fence = GenerationFence(
                "production-20261009", "2026-10-09", "a" * 40, 300,
                "phase8-2026-10-09", root / "recovery",
            )
            unavailable = GenerationUnavailable(
                "123", "completed", "success", ReasonCode.ARTIFACT_DELAYED, True
            )
            common = {
                "DATABASE_PATH": root / "operations.db",
                "LEDGER_PATH": root / "ledger.jsonl",
                "ORCHESTRATOR_HEARTBEAT_PATH": heartbeat,
            }
            for _ in range(2):
                with patch.multiple("room_phase8_runner", **common), patch(
                    "room_phase8_runner.generation_fence", return_value=fence
                ), patch("room_phase8_runner.github_token", return_value="token"), patch(
                    "room_phase8_runner.github_headers", return_value={}
                ), patch("room_phase8_runner.dispatch_generation_once", return_value=False), patch(
                    "room_phase8_runner.fetch_fenced_artifact", return_value=unavailable
                ):
                    result = run_phase8(now=NOW, apply=False, session=Session())
                self.assertEqual(result["status"], "generation_failed")
                self.assertEqual(result["failure_reason"], ReasonCode.ARTIFACT_DELAYED.value)
                self.assertEqual(json.loads(heartbeat.read_text())["status"], "failed")
            snapshot = store.export_snapshot("2026-10-09")
            self.assertEqual(len(snapshot["incidents"]), 1)
            self.assertEqual(
                snapshot["incidents"][0]["reason_code"],
                ReasonCode.ARTIFACT_DELAYED.value,
            )

    def test_ambiguous_no_artifact_runs_do_not_create_a_terminal_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, fence = self._local_artifact(Path(temp))
            fence.manifest_path.unlink()

            class AmbiguousSession(WorkflowRunWithoutArtifactSession):
                def get(self, url, **kwargs):
                    response = super().get(url, **kwargs)
                    if url.endswith("/actions/workflows/room-fenced-recovery.yml/runs"):
                        value = dict(response.json()["workflow_runs"][0])
                        second = dict(value, id=124, created_at="2026-10-09T03:06:00Z")
                        return Response({"workflow_runs": [second, value]})
                    return response

            result = fetch_fenced_artifact(
                AmbiguousSession(status="completed", conclusion="failure"),
                headers={}, store=store, fence=fence, now=NOW,
            )
            self.assertIsInstance(result, GenerationUnavailable)
            self.assertFalse(result.terminal)
            self.assertEqual(result.reason, ReasonCode.ACTIONS_RUN_DELAYED)

    def test_failed_run_without_artifact_records_one_incident_and_failed_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            heartbeat = root / "orchestrator-heartbeat.json"
            store = RoomStateStore(root / "operations.db")
            store.reserve_recovery_control(
                recovery_id="production-20261009", routine_date="2026-10-09",
                old_run_id="37579267266", expected_head_sha="a" * 40,
                revision=300, owner_id="phase8-2026-10-09", now=NOW,
            )
            fence = GenerationFence(
                "production-20261009", "2026-10-09", "a" * 40, 300,
                "phase8-2026-10-09", root / "recovery",
            )
            unavailable = GenerationUnavailable(
                "123", "completed", "failure", ReasonCode.ACTIONS_RUN_FAILED, True
            )
            common = {
                "DATABASE_PATH": root / "operations.db",
                "LEDGER_PATH": root / "ledger.jsonl",
                "ORCHESTRATOR_HEARTBEAT_PATH": heartbeat,
            }
            for _ in range(2):
                with patch.multiple("room_phase8_runner", **common), patch(
                    "room_phase8_runner.generation_fence", return_value=fence
                ), patch("room_phase8_runner.github_token", return_value="token"), patch(
                    "room_phase8_runner.github_headers", return_value={}
                ), patch("room_phase8_runner.dispatch_generation_once", return_value=False), patch(
                    "room_phase8_runner.fetch_fenced_artifact", return_value=unavailable
                ):
                    result = run_phase8(now=NOW, apply=False, session=Session())
                self.assertEqual(result["status"], "generation_failed")
                self.assertEqual(json.loads(heartbeat.read_text())["status"], "failed")
            snapshot = store.export_snapshot("2026-10-09")
            self.assertEqual(len(snapshot["incidents"]), 1)
            self.assertEqual(
                snapshot["incidents"][0]["reason_code"],
                ReasonCode.ACTIONS_RUN_FAILED.value,
            )
            self.assertEqual(store.get_recovery_control(fence.recovery_id)["status"], "failed")

    def test_quality_block_and_failed_actions_share_one_idempotent_incident(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = RoomStateStore(Path(temp) / "operations.db")
            store.initialize()
            value = phase8_manifest()
            value["slots"]["evening"] = {
                "status": "blocked",
                "reason": "missing_quality_safe_candidate",
                "candidate": None,
            }
            store.accept_manifest(value, now=NOW)
            artifact = FencedArtifact(
                Path(temp) / "manifest.json",
                "123",
                "completed",
                "failure",
            )
            first = record_generation_incidents(store, value, artifact, now=NOW)
            store.transition_incident(
                first[0],
                expected_status=IncidentStatus.OPEN,
                target_status=IncidentStatus.NEEDS_HUMAN,
                next_action="approved review",
                now=NOW,
            )
            second = record_generation_incidents(store, value, artifact, now=NOW)
            snapshot = store.export_snapshot("2026-10-09")
            self.assertEqual(first, second)
            self.assertEqual(len(snapshot["incidents"]), 1)
            self.assertEqual(
                snapshot["incidents"][0]["reason_code"],
                ReasonCode.QUALITY_POOL_EXHAUSTED.value,
            )

    def test_run_terminalizes_heartbeat_on_pending_and_exception(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            heartbeat = root / "orchestrator-heartbeat.json"
            fence = GenerationFence(
                "production-20261009", "2026-10-09", "a" * 40, 300,
                "phase8-2026-10-09", root / "recovery",
            )
            common = {
                "DATABASE_PATH": root / "operations.db",
                "LEDGER_PATH": root / "ledger.jsonl",
                "ORCHESTRATOR_HEARTBEAT_PATH": heartbeat,
            }
            with patch.multiple("room_phase8_runner", **common), patch(
                "room_phase8_runner.generation_fence", return_value=fence
            ), patch("room_phase8_runner.github_token", return_value="token"), patch(
                "room_phase8_runner.github_headers", return_value={}
            ), patch("room_phase8_runner.dispatch_generation_once", return_value=False), patch(
                "room_phase8_runner.fetch_fenced_artifact", return_value=None
            ):
                result = run_phase8(now=NOW, apply=False, session=Session())
            self.assertEqual(result["status"], "generation_pending")
            self.assertEqual(json.loads(heartbeat.read_text())["status"], "generation_pending")

            with patch.multiple("room_phase8_runner", **common), patch(
                "room_phase8_runner.generation_fence", side_effect=RuntimeError("boom")
            ):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    run_phase8(now=NOW, apply=False, session=Session())
            failed = json.loads(heartbeat.read_text())
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["error_code"], "RuntimeError")

    def test_generation_fence_uses_latest_replacement_identity_and_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = RoomStateStore(Path(temp) / "operations.db")
            store.reserve_recovery_control(
                recovery_id="production-20261009", routine_date="2026-10-09",
                old_run_id="37579267266", expected_head_sha="a" * 40,
                revision=300, owner_id="base", now=NOW,
            )
            store.reserve_recovery_control(
                recovery_id="production-20261009-quality1", routine_date="2026-10-09",
                old_run_id="37579267266", expected_head_sha="b" * 40,
                revision=301, owner_id="replacement", now=NOW,
            )
            fence = generation_fence(store, NOW)
            self.assertEqual(fence.recovery_id, "production-20261009-quality1")
            self.assertEqual(fence.revision, 301)
            self.assertEqual(fence.head_sha, "b" * 40)

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
            self.assertEqual(inputs["blocked_urls"], "")
            self.assertEqual(json.loads(fence.control_path.read_text())["status"], "dispatched")

    def test_pre_dispatch_head_rebind_is_bounded_and_keeps_single_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = RoomStateStore(root / "operations.db")
            store.reserve_recovery_control(
                recovery_id="production-20261011", routine_date="2026-10-11",
                old_run_id="37579267266", expected_head_sha="b" * 40,
                revision=303, owner_id="phase8-2026-10-11", now=NOW,
            )
            fence = GenerationFence(
                "production-20261011", "2026-10-11", "b" * 40, 303,
                "phase8-2026-10-11", root / "recovery",
            )
            session = Session()
            with patch("room_phase8_runner._git_head", return_value="a" * 40):
                self.assertTrue(dispatch_generation_once(
                    session, headers={}, store=store, fence=fence, now=NOW
                ))
            self.assertEqual(fence.head_sha, "a" * 40)
            control = store.get_recovery_control("production-20261011")
            self.assertEqual(control["expected_head_sha"], "a" * 40)
            self.assertEqual(control["budget_consumed"], 1)
            self.assertEqual(session.posts[0][1]["inputs"]["expected_head_sha"], "a" * 40)

    def test_uncertain_prior_slot_does_not_block_later_independent_slot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = RoomStateStore(Path(temp) / "operations.db")
            value = phase8_manifest()
            store.initialize()
            store.accept_manifest(value, now=NOW)
            morning = store.get_slot("2026-10-09", "morning")
            store.transition_slot(
                "2026-10-09", "morning", expected_status=morning.status,
                expected_version=morning.version, target_status=SlotStatus.CLAIMED, now=NOW,
            )
            store.transition_slot(
                "2026-10-09", "morning", expected_status=SlotStatus.CLAIMED,
                expected_version=morning.version + 1, target_status=SlotStatus.SUBMITTING, now=NOW,
            )
            store.transition_slot(
                "2026-10-09", "morning", expected_status=SlotStatus.SUBMITTING,
                expected_version=morning.version + 2, target_status=SlotStatus.UNCERTAIN, now=NOW,
            )
            self.assertEqual(next_eligible_slot(value, store, NOW), "noon")

    def test_in_flight_prior_slot_still_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = RoomStateStore(Path(temp) / "operations.db")
            value = phase8_manifest()
            store.initialize()
            store.accept_manifest(value, now=NOW)
            morning = store.get_slot("2026-10-09", "morning")
            store.transition_slot(
                "2026-10-09", "morning", expected_status=morning.status,
                expected_version=morning.version, target_status=SlotStatus.CLAIMED, now=NOW,
            )
            store.transition_slot(
                "2026-10-09", "morning", expected_status=SlotStatus.CLAIMED,
                expected_version=morning.version + 1, target_status=SlotStatus.SUBMITTING, now=NOW,
            )
            store.transition_slot(
                "2026-10-09", "morning", expected_status=SlotStatus.SUBMITTING,
                expected_version=morning.version + 2, target_status=SlotStatus.UNCERTAIN, now=NOW,
            )
            noon = store.get_slot("2026-10-09", "noon")
            store.transition_slot(
                "2026-10-09", "noon", expected_status=noon.status,
                expected_version=noon.version, target_status=SlotStatus.CLAIMED, now=NOW,
            )
            with self.assertRaisesRegex(RuntimeError, "CLAIMED"):
                next_eligible_slot(value, store, NOW)

    def test_uncertain_morning_allows_ordered_noon_then_evening(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = RoomStateStore(Path(temp) / "operations.db")
            value = phase8_manifest()
            store.initialize()
            store.accept_manifest(value, now=NOW)
            morning = store.get_slot("2026-10-09", "morning")
            for source, target, offset in (
                (SlotStatus.READY, SlotStatus.CLAIMED, 0),
                (SlotStatus.CLAIMED, SlotStatus.SUBMITTING, 1),
                (SlotStatus.SUBMITTING, SlotStatus.UNCERTAIN, 2),
            ):
                store.transition_slot(
                    "2026-10-09", "morning", expected_status=source,
                    expected_version=morning.version + offset,
                    target_status=target, now=NOW,
                )
            noon_now = datetime(2026, 10, 9, 3, 5, tzinfo=timezone.utc)
            self.assertEqual(next_eligible_slot(value, store, noon_now), "noon")
            execute(
                manifest=value, slot="noon", active_slots=set(POST_SLOTS), now=noon_now,
                apply=True, store=store, gateway=Gateway(),
                legacy_ledger_path=Path(temp) / "ledger.jsonl",
            )
            evening_now = datetime(2026, 10, 9, 10, 5, tzinfo=timezone.utc)
            self.assertEqual(next_eligible_slot(value, store, evening_now), "evening")

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

    def test_terminal_budget_exhausted_incident_does_not_fail_later_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = RoomStateStore(root / "operations.db")
            ledger = root / "ledger.jsonl"
            value = phase8_manifest()
            evening = datetime(2026, 10, 9, 10, 5, tzinfo=timezone.utc)
            for slot in POST_SLOTS:
                result = execute(
                    manifest=value, slot=slot, active_slots=set(POST_SLOTS), now=evening,
                    apply=True, store=store, gateway=Gateway(), legacy_ledger_path=ledger,
                )
                self.assertEqual(result["status"], SlotStatus.POSTED.value)
            incident = store.create_incident(
                "2026-10-09", reason=ReasonCode.COPY_VALIDATION_REGRESSION,
                last_safe_state=SlotStatus.BLOCKED.value,
                next_action="bounded budget exhausted", slot="evening",
                manifest_revision=300, now=NOW,
            )
            store.transition_incident(
                incident, expected_status=IncidentStatus.OPEN,
                target_status=IncidentStatus.BUDGET_EXHAUSTED,
                next_action="terminal audit record", now=NOW,
            )
            self.assertTrue(audit_day(store, ledger, "2026-10-09")["ok"])


if __name__ == "__main__":
    unittest.main()
