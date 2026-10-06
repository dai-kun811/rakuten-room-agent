from __future__ import annotations

import hashlib
import unittest
from datetime import datetime, timezone

from room_operation_contract import (
    ContractError,
    DEFAULT_RETRY_BUDGET,
    IncidentRoute,
    IncidentStatus,
    ReasonCode,
    SlotStatus,
    TransitionError,
    build_idempotency_key,
    incident_fingerprint,
    route_for_reason,
    routine_date_jst,
    validate_incident_transition,
    validate_manifest_v2,
    validate_slot_transition,
)


def candidate(url: str = "https://item.rakuten.co.jp/shop/item-1") -> dict:
    body = "確認済みの特徴と購入前の確認点を含む本文です。"
    return {
        "product_url": url,
        "normalized_url": url,
        "product_name": "テスト商品",
        "product_type": "wipes",
        "title": "おしりふき｜外出時の補充",
        "body": body,
        "hashtags": ["#おしりふき"],
        "content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "quality": {"status": "passed", "errors": []},
    }


def manifest() -> dict:
    return {
        "schema_version": 2,
        "routine_date_jst": "2026-10-06",
        "revision": 1,
        "supersedes_revision": None,
        "actions_run_id": "123456",
        "head_sha": "a" * 40,
        "generated_at": "2026-10-06T07:00:00+09:00",
        "slots": {
            "morning": {"status": "ready", "candidate": candidate()},
            "noon": {"status": "blocked", "reason": "quality_exhausted"},
            "evening": {
                "status": "ready",
                "candidate": candidate("https://item.rakuten.co.jp/shop/item-2"),
            },
        },
    }


class RoomOperationContractTests(unittest.TestCase):
    def test_routine_date_is_always_jst(self) -> None:
        self.assertEqual(
            routine_date_jst(datetime(2026, 10, 5, 15, 30, tzinfo=timezone.utc)).isoformat(),
            "2026-10-06",
        )

    def test_routine_date_rejects_naive_datetime(self) -> None:
        with self.assertRaises(ContractError):
            routine_date_jst(datetime(2026, 10, 6, 7, 0))

    def test_safe_posting_path_is_allowed(self) -> None:
        path = [
            SlotStatus.PENDING,
            SlotStatus.READY,
            SlotStatus.CLAIMED,
            SlotStatus.SUBMITTING,
            SlotStatus.SUBMITTED_UNCONFIRMED,
            SlotStatus.POSTED,
        ]
        for source, target in zip(path, path[1:]):
            validate_slot_transition(source, target)

    def test_posted_is_terminal(self) -> None:
        with self.assertRaises(TransitionError):
            validate_slot_transition(SlotStatus.POSTED, SlotStatus.READY)

    def test_submitting_cannot_return_to_ready(self) -> None:
        with self.assertRaises(TransitionError):
            validate_slot_transition(SlotStatus.SUBMITTING, SlotStatus.READY)

    def test_uncertain_requires_manual_resolution_before_retry(self) -> None:
        with self.assertRaises(TransitionError):
            validate_slot_transition(SlotStatus.UNCERTAIN, SlotStatus.FAILED_PRE_SUBMIT)
        validate_slot_transition(
            SlotStatus.UNCERTAIN,
            SlotStatus.FAILED_PRE_SUBMIT,
            manual_resolution=True,
        )

    def test_incident_recovery_path_is_bounded(self) -> None:
        validate_incident_transition(IncidentStatus.OPEN, IncidentStatus.AUTO_RECOVERING)
        validate_incident_transition(
            IncidentStatus.AUTO_RECOVERING, IncidentStatus.CODEX_QUEUED
        )
        validate_incident_transition(IncidentStatus.CODEX_QUEUED, IncidentStatus.CODEX_WORKING)
        validate_incident_transition(IncidentStatus.CODEX_WORKING, IncidentStatus.VALIDATING)
        validate_incident_transition(IncidentStatus.VALIDATING, IncidentStatus.RESOLVED)
        with self.assertRaises(TransitionError):
            validate_incident_transition(IncidentStatus.RESOLVED, IncidentStatus.OPEN)

    def test_reason_routes_keep_human_only_cases_out_of_automatic_recovery(self) -> None:
        self.assertEqual(route_for_reason(ReasonCode.RATE_LIMITED), IncidentRoute.AUTO_RETRY)
        self.assertEqual(
            route_for_reason(ReasonCode.QUALITY_POOL_EXHAUSTED),
            IncidentRoute.CODEX_RECOMMENDED,
        )
        self.assertEqual(
            route_for_reason(ReasonCode.POST_RESULT_UNCERTAIN),
            IncidentRoute.USER_ACTION_REQUIRED,
        )

    def test_idempotency_key_ignores_url_query_and_trailing_slash(self) -> None:
        digest = "b" * 64
        first = build_idempotency_key(
            "2026-10-06",
            "morning",
            "https://item.rakuten.co.jp/shop/item/?scid=test",
            digest,
        )
        second = build_idempotency_key(
            "2026-10-06",
            "morning",
            "https://ITEM.RAKUTEN.CO.JP/shop/item",
            digest,
        )
        self.assertEqual(first, second)

    def test_incident_fingerprint_is_stable(self) -> None:
        first = incident_fingerprint(
            ReasonCode.QUALITY_POOL_EXHAUSTED,
            "2026-10-06",
            slot="noon",
            component="generator",
        )
        second = incident_fingerprint(
            "quality_pool_exhausted",
            "2026-10-06",
            slot="noon",
            component="generator",
        )
        self.assertEqual(first, second)

    def test_manifest_accepts_independent_ready_and_blocked_slots(self) -> None:
        validate_manifest_v2(manifest())

    def test_manifest_rejects_missing_slot(self) -> None:
        payload = manifest()
        del payload["slots"]["evening"]
        with self.assertRaises(ContractError):
            validate_manifest_v2(payload)

    def test_manifest_rejects_ready_candidate_with_quality_errors(self) -> None:
        payload = manifest()
        payload["slots"]["morning"]["candidate"]["quality"]["errors"] = ["unsafe"]
        with self.assertRaises(ContractError):
            validate_manifest_v2(payload)

    def test_manifest_rejects_normalized_url_mismatch(self) -> None:
        payload = manifest()
        payload["slots"]["morning"]["candidate"]["normalized_url"] = (
            "https://item.rakuten.co.jp/shop/other"
        )
        with self.assertRaises(ContractError):
            validate_manifest_v2(payload)

    def test_manifest_rejects_secret_like_fields(self) -> None:
        payload = manifest()
        payload["token"] = "forbidden"
        with self.assertRaises(ContractError):
            validate_manifest_v2(payload)

    def test_manifest_rejects_non_older_superseded_revision(self) -> None:
        payload = manifest()
        payload["revision"] = 2
        payload["supersedes_revision"] = 2
        with self.assertRaises(ContractError):
            validate_manifest_v2(payload)

    def test_retry_budget_matches_approved_limits(self) -> None:
        self.assertEqual(DEFAULT_RETRY_BUDGET.transient_attempts, 2)
        self.assertEqual(DEFAULT_RETRY_BUDGET.actions_dispatches, 1)
        self.assertEqual(DEFAULT_RETRY_BUDGET.pre_submit_retries, 1)
        self.assertEqual(DEFAULT_RETRY_BUDGET.codex_fix_cycles, 2)
        self.assertEqual(DEFAULT_RETRY_BUDGET.codex_minutes_per_incident, 45)
        self.assertEqual(DEFAULT_RETRY_BUDGET.codex_minutes_per_day, 60)


if __name__ == "__main__":
    unittest.main()
