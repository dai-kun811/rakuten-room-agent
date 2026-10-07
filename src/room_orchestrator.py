from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from room_operation_contract import IncidentStatus, ReasonCode, SlotStatus, posting_window_open
from room_state_store import RoomStateStore, StateConflictError


class Confirmation(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class PreSubmitFailure(RuntimeError):
    pass


class AuthenticationRequired(PreSubmitFailure):
    pass


class CaptchaRequired(PreSubmitFailure):
    pass


class AccountRestricted(PreSubmitFailure):
    pass


class InjectedCrash(BaseException):
    """Test-only abrupt process loss; deliberately bypasses recovery handlers."""


class PostGateway(Protocol):
    def submit(
        self,
        candidate: Mapping[str, Any],
        *,
        before_submit: Callable[[], None],
        after_submit: Callable[[], None],
    ) -> Confirmation: ...


@dataclass(frozen=True)
class ExecutionOutcome:
    routine_date: str
    slot: str
    status: SlotStatus
    attempt_id: str | None
    incident_id: str | None = None


class RoomOrchestrator:
    """Single-slot state owner. It delegates external I/O to a gateway."""

    def __init__(
        self,
        store: RoomStateStore,
        gateway: PostGateway,
        *,
        legacy_ledger_path: Path | None = None,
        failure_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.store = store
        self.gateway = gateway
        self.legacy_ledger_path = legacy_ledger_path
        self.failure_hook = failure_hook or (lambda _stage: None)

    def execute_slot(
        self,
        routine_date: str,
        slot: str,
        candidate: Mapping[str, Any],
        *,
        now: datetime,
    ) -> ExecutionOutcome:
        with self.store.execution_lock:
            return self._execute_slot(routine_date, slot, candidate, now=now)

    def _execute_slot(
        self,
        routine_date: str,
        slot: str,
        candidate: Mapping[str, Any],
        *,
        now: datetime,
    ) -> ExecutionOutcome:
        current = self.store.get_slot(routine_date, slot)
        if current is None:
            raise StateConflictError(f"slot does not exist: {routine_date}:{slot}")
        if current.status is SlotStatus.POSTED:
            if self.legacy_ledger_path is not None:
                self.store.sync_legacy_ledger(
                    self.legacy_ledger_path, routine_date=routine_date, now=now
                )
            return ExecutionOutcome(routine_date, slot, SlotStatus.POSTED, None)
        if current.status in {
            SlotStatus.SUBMITTING,
            SlotStatus.SUBMITTED_UNCONFIRMED,
            SlotStatus.UNCERTAIN,
        }:
            attempt = self.store.get_active_post_attempt(routine_date, slot)
            if current.status is not SlotStatus.UNCERTAIN and attempt is not None:
                incident = self._mark_uncertain(
                    routine_date, slot, str(attempt["attempt_id"]),
                    current.manifest_revision, current.status, now,
                )
                return ExecutionOutcome(
                    routine_date, slot, SlotStatus.UNCERTAIN,
                    str(attempt["attempt_id"]), incident,
                )
            incident = self._human_incident(
                routine_date, slot, current.manifest_revision,
                ReasonCode.POST_RESULT_UNCERTAIN, current.status.value, now,
            )
            return ExecutionOutcome(routine_date, slot, current.status, None, incident)
        if current.status is not SlotStatus.READY:
            raise StateConflictError(f"slot is not executable: {current.status.value}")
        if not posting_window_open(routine_date, now):
            expired = self.store.transition_slot(
                routine_date, slot, expected_status=SlotStatus.READY,
                expected_version=current.version,
                target_status=SlotStatus.EXPIRED_UNPOSTED,
                payload={"reason": "posting_window_closed"}, now=now,
            )
            return ExecutionOutcome(routine_date, slot, expired.status, None)
        if str(candidate.get("normalized_url", "")) != current.normalized_url:
            raise StateConflictError("candidate URL does not match accepted manifest")
        if str(candidate.get("content_hash", "")) != current.content_hash:
            raise StateConflictError("candidate content hash does not match accepted manifest")

        attempt = self.store.claim_post_attempt(
            routine_date, slot, expected_version=current.version, now=now
        )
        self.failure_hook("after_claim")

        def before_submit() -> None:
            self.failure_hook("before_submitting_commit")
            self.store.advance_post_attempt(
                attempt.attempt_id,
                expected_slot_status=SlotStatus.CLAIMED,
                target_slot_status=SlotStatus.SUBMITTING,
                expected_attempt_status=SlotStatus.CLAIMED.value,
                target_attempt_status=SlotStatus.SUBMITTING.value,
                submit_started=True,
                now=now,
            )
            self.failure_hook("after_submitting_commit")

        def after_submit() -> None:
            self.store.advance_post_attempt(
                attempt.attempt_id,
                expected_slot_status=SlotStatus.SUBMITTING,
                target_slot_status=SlotStatus.SUBMITTED_UNCONFIRMED,
                expected_attempt_status=SlotStatus.SUBMITTING.value,
                target_attempt_status=SlotStatus.SUBMITTED_UNCONFIRMED.value,
                submit_started=True,
                now=now,
            )
            self.failure_hook("after_submitted_commit")

        try:
            confirmation = self.gateway.submit(
                candidate, before_submit=before_submit, after_submit=after_submit
            )
        except InjectedCrash:
            raise
        except Exception as exc:
            return self._handle_gateway_failure(
                routine_date, slot, attempt.attempt_id, current.manifest_revision, exc, now
            )

        state = self.store.get_slot(routine_date, slot)
        assert state is not None
        if confirmation is not Confirmation.PRESENT or state.status is not SlotStatus.SUBMITTED_UNCONFIRMED:
            incident = self._mark_uncertain(
                routine_date, slot, attempt.attempt_id, current.manifest_revision, state.status, now
            )
            return ExecutionOutcome(routine_date, slot, SlotStatus.UNCERTAIN, attempt.attempt_id, incident)

        self.failure_hook("before_posted_commit")
        posted = self.store.advance_post_attempt(
            attempt.attempt_id,
            expected_slot_status=SlotStatus.SUBMITTED_UNCONFIRMED,
            target_slot_status=SlotStatus.POSTED,
            expected_attempt_status=SlotStatus.SUBMITTED_UNCONFIRMED.value,
            target_attempt_status=SlotStatus.POSTED.value,
            submit_started=True,
            now=now,
        )
        self.failure_hook("after_posted_commit")
        if self.legacy_ledger_path is not None:
            self.store.sync_legacy_ledger(
                self.legacy_ledger_path, routine_date=routine_date, now=now
            )
        return ExecutionOutcome(routine_date, slot, posted.status, attempt.attempt_id)

    def _handle_gateway_failure(
        self,
        day: str,
        slot: str,
        attempt_id: str,
        manifest_revision: int | None,
        exc: Exception,
        now: datetime,
    ) -> ExecutionOutcome:
        state = self.store.get_slot(day, slot)
        assert state is not None
        if state.status is SlotStatus.CLAIMED:
            self.store.advance_post_attempt(
                attempt_id,
                expected_slot_status=SlotStatus.CLAIMED,
                target_slot_status=SlotStatus.FAILED_PRE_SUBMIT,
                expected_attempt_status=SlotStatus.CLAIMED.value,
                target_attempt_status=SlotStatus.FAILED_PRE_SUBMIT.value,
                submit_started=False,
                now=now,
            )
            reason = self._pre_submit_reason(exc)
            incident = self.store.create_incident(
                day, reason=reason, slot=slot, component="orchestrator",
                last_safe_state=SlotStatus.FAILED_PRE_SUBMIT.value,
                next_action="human review" if reason is not ReasonCode.FAILED_PRE_SUBMIT else "bounded pre-submit retry",
                manifest_revision=manifest_revision, now=now,
            )
            target = (
                IncidentStatus.AUTO_RECOVERING
                if reason is ReasonCode.FAILED_PRE_SUBMIT
                else IncidentStatus.NEEDS_HUMAN
            )
            self.store.transition_incident(
                incident, expected_status=IncidentStatus.OPEN, target_status=target,
                next_action="bounded pre-submit retry" if target is IncidentStatus.AUTO_RECOVERING else "wait for human",
                now=now,
            )
            return ExecutionOutcome(day, slot, SlotStatus.FAILED_PRE_SUBMIT, attempt_id, incident)
        incident = self._mark_uncertain(
            day, slot, attempt_id, manifest_revision, state.status, now
        )
        return ExecutionOutcome(day, slot, SlotStatus.UNCERTAIN, attempt_id, incident)

    def _mark_uncertain(
        self,
        day: str,
        slot: str,
        attempt_id: str,
        manifest_revision: int | None,
        status: SlotStatus,
        now: datetime,
    ) -> str:
        if status is SlotStatus.SUBMITTING:
            attempt_status = SlotStatus.SUBMITTING.value
        elif status is SlotStatus.SUBMITTED_UNCONFIRMED:
            attempt_status = SlotStatus.SUBMITTED_UNCONFIRMED.value
        elif status is SlotStatus.UNCERTAIN:
            incident = self._human_incident(
                day, slot, manifest_revision, ReasonCode.POST_RESULT_UNCERTAIN,
                SlotStatus.UNCERTAIN.value, now,
            )
            return incident
        else:
            raise StateConflictError(f"cannot mark uncertain from {status.value}")
        self.store.advance_post_attempt(
            attempt_id,
            expected_slot_status=status,
            target_slot_status=SlotStatus.UNCERTAIN,
            expected_attempt_status=attempt_status,
            target_attempt_status=SlotStatus.UNCERTAIN.value,
            submit_started=True,
            now=now,
        )
        return self._human_incident(
            day, slot, manifest_revision, ReasonCode.POST_RESULT_UNCERTAIN,
            status.value, now,
        )

    def _human_incident(
        self,
        day: str,
        slot: str,
        manifest_revision: int | None,
        reason: ReasonCode,
        last_safe_state: str,
        now: datetime,
    ) -> str:
        incident = self.store.create_incident(
            day, reason=reason, slot=slot, component="orchestrator",
            last_safe_state=last_safe_state, next_action="wait for human",
            manifest_revision=manifest_revision, now=now,
        )
        row = self.store.get_incident(incident)
        if row is not None and row["status"] == IncidentStatus.OPEN.value:
            self.store.transition_incident(
                incident, expected_status=IncidentStatus.OPEN,
                target_status=IncidentStatus.NEEDS_HUMAN,
                next_action="wait for human", now=now,
            )
        return incident

    @staticmethod
    def _pre_submit_reason(exc: Exception) -> ReasonCode:
        if isinstance(exc, AuthenticationRequired):
            return ReasonCode.AUTH_EXPIRED
        if isinstance(exc, CaptchaRequired):
            return ReasonCode.CAPTCHA
        if isinstance(exc, AccountRestricted):
            return ReasonCode.ACCOUNT_RESTRICTED
        return ReasonCode.FAILED_PRE_SUBMIT
