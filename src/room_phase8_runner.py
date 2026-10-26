from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import subprocess
import uuid
import zipfile
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Mapping

from local_room_worker import REPO_API, github_headers, github_token
from room_operation_contract import (
    JST,
    POST_SLOTS,
    ReasonCode,
    SlotStatus,
    posting_window_open,
    routine_date_jst,
)
from room_orchestrator_worker import DATABASE_PATH, LEDGER_PATH, execute, load_fenced_manifest
from room_manifest_v2 import candidate_content_hash
from room_state_store import RoomStateStore
from room_engagement_state import sanitized_heartbeat


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = PROJECT_ROOT / ".local" / "room-worker"
RECOVERY_ROOT = STATE_ROOT / "generation-recoveries"
LOG_PATH = STATE_ROOT / "phase8-orchestrator.log"
ORCHESTRATOR_HEARTBEAT_PATH = STATE_ROOT / "orchestrator-heartbeat.json"
WORKFLOW_FILE = "room-fenced-recovery.yml"
OLD_FENCED_RUN_ID = "37579267266"
AUDIT_TIME = time(20, 30)
ARTIFACT_GRACE_PERIOD = timedelta(minutes=15)


@dataclass
class GenerationFence:
    recovery_id: str
    routine_date: str
    head_sha: str
    revision: int
    owner_id: str
    directory: Path

    @property
    def control_path(self) -> Path:
        return self.directory / "control.json"

    @property
    def manifest_path(self) -> Path:
        return self.directory / "reports" / "room_operational_manifest_v2.json"

    @property
    def verification_path(self) -> Path:
        return self.directory / "manifest-verification.json"


@dataclass(frozen=True)
class FencedArtifact:
    path: Path
    actions_run_id: str
    actions_status: str
    actions_conclusion: str


@dataclass(frozen=True)
class GenerationUnavailable:
    actions_run_id: str
    actions_status: str
    actions_conclusion: str
    reason: ReasonCode
    terminal: bool


def _actions_run(session: Any, headers: Mapping[str, str], run_id: str | int) -> Mapping[str, Any]:
    response = session.get(
        f"{REPO_API}/actions/runs/{run_id}", headers=dict(headers), timeout=30
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise RuntimeError("Actions run response is not an object")
    return payload


def _dispatched_at(fence: GenerationFence) -> datetime | None:
    if not fence.control_path.exists():
        return None
    try:
        control = json.loads(fence.control_path.read_text(encoding="utf-8"))
        value = str(control.get("dispatched_at") or control.get("created_at") or "")
        parsed = datetime.fromisoformat(value)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _plausible_dispatched_runs(
    runs: list[Mapping[str, Any]], fence: GenerationFence
) -> list[Mapping[str, Any]]:
    dispatched_at = _dispatched_at(fence)
    plausible: list[Mapping[str, Any]] = []
    for run in runs:
        if str(run.get("head_sha", "")) != fence.head_sha:
            continue
        if run.get("event") not in (None, "workflow_dispatch"):
            continue
        created_text = str(run.get("created_at") or "")
        try:
            created_at = datetime.fromisoformat(created_text.replace("Z", "+00:00"))
        except ValueError:
            # An artifact name can still bind a run later, but a run without
            # creation time is not strong enough evidence for a no-artifact
            # failure incident.
            continue
        if dispatched_at is not None and created_at < dispatched_at:
            continue
        plausible.append(run)
    return plausible


def _is_partial_manifest(manifest: Mapping[str, Any], artifact: FencedArtifact) -> bool:
    return artifact.actions_conclusion != "success" or any(
        manifest["slots"][slot]["status"] == "blocked" for slot in POST_SLOTS
    )


def _manifest_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_candidate_hashes(manifest: Mapping[str, Any]) -> None:
    for slot in POST_SLOTS:
        value = manifest["slots"][slot]
        if value["status"] != "ready":
            continue
        candidate = value["candidate"]
        if str(candidate.get("content_hash", "")) != candidate_content_hash(candidate):
            raise RuntimeError(f"manifest candidate content hash mismatch: {slot}")


def _verification_expiry(fence: GenerationFence) -> datetime:
    return datetime.combine(date.fromisoformat(fence.routine_date), time(22, 30), tzinfo=JST)


def _artifact_grace_exhausted(run: Mapping[str, Any], now: datetime) -> bool:
    """Return whether a completed run has exhausted artifact propagation time.

    GitHub's workflow-run payload includes ``updated_at`` for the terminal run
    update.  A completed run without a usable terminal timestamp cannot prove
    that it is still inside the bounded grace period, so it fails closed.
    """
    try:
        completed_at = datetime.fromisoformat(
            str(run.get("updated_at") or "").replace("Z", "+00:00")
        )
    except ValueError:
        return True
    if completed_at.tzinfo is None or completed_at.utcoffset() is None:
        return True
    return now >= completed_at + ARTIFACT_GRACE_PERIOD


def _store_manifest_verification(
    fence: GenerationFence,
    artifact: FencedArtifact,
    *,
    now: datetime,
) -> None:
    _atomic_json(
        fence.verification_path,
        {
            "schema_version": 1,
            "recovery_id": fence.recovery_id,
            "routine_date": fence.routine_date,
            "expected_head_sha": fence.head_sha,
            "revision": fence.revision,
            "actions_run_id": artifact.actions_run_id,
            "actions_status": artifact.actions_status,
            "actions_conclusion": artifact.actions_conclusion,
            "manifest_sha256": _manifest_digest(fence.manifest_path),
            "verified_at": now.astimezone(JST).isoformat(),
            "valid_until": _verification_expiry(fence).isoformat(),
        },
    )


def _load_verified_local_artifact(
    *,
    store: RoomStateStore,
    fence: GenerationFence,
    now: datetime,
) -> FencedArtifact | None:
    if not fence.manifest_path.exists() or not fence.verification_path.exists():
        return None
    verification = json.loads(fence.verification_path.read_text(encoding="utf-8"))
    expected = {
        "schema_version": 1,
        "recovery_id": fence.recovery_id,
        "routine_date": fence.routine_date,
        "expected_head_sha": fence.head_sha,
        "revision": fence.revision,
        "actions_status": "completed",
    }
    if any(verification.get(key) != value for key, value in expected.items()):
        raise RuntimeError("verified local manifest metadata does not match the active fence")
    if routine_date_jst(now).isoformat() != fence.routine_date:
        raise RuntimeError("verified local manifest belongs to a different routine date")
    expected_expiry = _verification_expiry(fence)
    if verification.get("valid_until") != expected_expiry.isoformat():
        raise RuntimeError("verified local manifest expiry does not match the active fence")
    if now.astimezone(JST) >= expected_expiry:
        raise RuntimeError("verified local manifest has expired")
    digest = str(verification.get("manifest_sha256", ""))
    if not digest or digest != _manifest_digest(fence.manifest_path):
        raise RuntimeError("verified local manifest integrity check failed")
    manifest = load_fenced_manifest(
        fence.manifest_path,
        expected_head_sha=fence.head_sha,
        expected_recovery_id=fence.recovery_id,
        expected_revision=fence.revision,
        state_store=store,
    )
    _validate_candidate_hashes(manifest)
    run_id = str(verification.get("actions_run_id", ""))
    if not run_id or str(manifest["actions_run_id"]) != run_id:
        raise RuntimeError("verified local manifest run ID does not match")
    control = store.get_recovery_control(fence.recovery_id)
    if control is None or str(control.get("replacement_run_id") or "") != run_id:
        raise RuntimeError("verified local manifest is not bound to its recovery control")
    return FencedArtifact(
        fence.manifest_path,
        run_id,
        "completed",
        str(verification.get("actions_conclusion") or ""),
    )


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, check=True,
        capture_output=True, text=True,
    )
    return result.stdout.strip()


def _remote_main_head(session: Any, headers: Mapping[str, str]) -> str:
    response = session.get(f"{REPO_API}/branches/main", headers=dict(headers), timeout=30)
    response.raise_for_status()
    return str(response.json()["commit"]["sha"])


def generation_fence(
    store: RoomStateStore, now: datetime, replacement_id: str | None = None
) -> GenerationFence:
    day = routine_date_jst(now).isoformat()
    recovery_id = replacement_id or f"production-{day.replace('-', '')}"
    existing = store.get_recovery_control(recovery_id) if replacement_id else store.latest_recovery_control(day)
    if existing is None:
        head = _git_head()
        revision = store.next_manifest_revision()
        owner = f"phase8-{day}"
        store.reserve_recovery_control(
            recovery_id=recovery_id,
            routine_date=day,
            old_run_id=OLD_FENCED_RUN_ID,
            expected_head_sha=head,
            revision=revision,
            owner_id=owner,
            lease_seconds=86400,
            now=now,
        )
    else:
        recovery_id = str(existing["recovery_id"])
        head = str(existing["expected_head_sha"])
        revision = int(existing["revision"])
        owner = str(existing.get("owner_id") or f"phase8-{day}")
    return GenerationFence(
        recovery_id=recovery_id,
        routine_date=day,
        head_sha=head,
        revision=revision,
        owner_id=owner,
        directory=RECOVERY_ROOT / recovery_id,
    )


def dispatch_generation_once(
    session: Any,
    *,
    headers: Mapping[str, str],
    store: RoomStateStore,
    fence: GenerationFence,
    now: datetime,
) -> bool:
    """Create a durable intent before the only allowed workflow dispatch."""
    if fence.control_path.exists():
        return False
    local_head = _git_head()
    remote_head = _remote_main_head(session, headers)
    if local_head != fence.head_sha or remote_head != fence.head_sha:
        # A pre-dispatch lease may have been reserved just before a normal
        # fast-forward. Rebind only while local/main agree and no dispatch
        # intent exists; never rebind after an external side effect begins.
        if (
            not fence.control_path.exists()
            and local_head == remote_head
            and local_head != fence.head_sha
        ):
            store.rebind_preflight_recovery_control(
                fence.recovery_id,
                owner_id=fence.owner_id,
                expected_head_sha=local_head,
                now=now,
            )
            fence.head_sha = local_head
        else:
            raise RuntimeError("local HEAD, recovery fence, and origin/main are not identical")
    if local_head != fence.head_sha or remote_head != fence.head_sha:
        raise RuntimeError("local HEAD, recovery fence, and origin/main are not identical")
    _atomic_json(
        fence.control_path,
        {
            "status": "dispatch_intent",
            "recovery_id": fence.recovery_id,
            "routine_date": fence.routine_date,
            "expected_head_sha": fence.head_sha,
            "revision": fence.revision,
            "created_at": now.astimezone(JST).isoformat(),
        },
    )
    inputs = {
        "recovery_id": fence.recovery_id,
        "expected_head_sha": fence.head_sha,
        "manifest_revision": str(fence.revision),
        "old_run_id": OLD_FENCED_RUN_ID,
        "posted_history_urls": "\n".join(store.posted_history_urls()),
        "blocked_urls": "\n".join(store.confirmed_absence_history_urls()),
    }
    response = session.post(
        f"{REPO_API}/actions/workflows/{WORKFLOW_FILE}/dispatches",
        headers=dict(headers),
        json={"ref": "main", "inputs": inputs},
        timeout=30,
    )
    response.raise_for_status()
    _atomic_json(
        fence.control_path,
        {
            "status": "dispatched",
            "recovery_id": fence.recovery_id,
            "routine_date": fence.routine_date,
            "expected_head_sha": fence.head_sha,
            "revision": fence.revision,
            "dispatched_at": now.astimezone(JST).isoformat(),
        },
    )
    return True


def fetch_fenced_artifact(
    session: Any,
    *,
    headers: Mapping[str, str],
    store: RoomStateStore,
    fence: GenerationFence,
    now: datetime,
) -> FencedArtifact | GenerationUnavailable | None:
    verified = _load_verified_local_artifact(store=store, fence=fence, now=now)
    if verified is not None:
        return verified
    if fence.manifest_path.exists():
        manifest = load_fenced_manifest(
            fence.manifest_path,
            expected_head_sha=fence.head_sha,
            expected_recovery_id=fence.recovery_id,
            expected_revision=fence.revision,
            state_store=store,
        )
        _validate_candidate_hashes(manifest)
        run_id = str(manifest["actions_run_id"])
        run = _actions_run(session, headers, run_id)
        if str(run.get("head_sha", "")) != fence.head_sha:
            raise RuntimeError("local fenced manifest Actions run has a different HEAD")
        status = str(run.get("status", ""))
        conclusion = str(run.get("conclusion") or "")
        if status != "completed":
            return GenerationUnavailable(
                run_id, status, conclusion, ReasonCode.ACTIONS_RUN_DELAYED, False
            )
        artifact = FencedArtifact(fence.manifest_path, run_id, status, conclusion)
        partial = _is_partial_manifest(manifest, artifact)
        control = store.get_recovery_control(fence.recovery_id)
        if control is not None and str(control.get("status", "")) == "running":
            store.complete_recovery_control(
                fence.recovery_id,
                owner_id=fence.owner_id,
                replacement_run_id=run_id,
                result_status="partial" if partial else "ready",
            )
        elif partial:
            if control is not None and str(control.get("status", "")) == "ready":
                store.mark_recovery_control_partial(
                    fence.recovery_id, replacement_run_id=run_id
                )
        _store_manifest_verification(fence, artifact, now=now)
        return artifact
    response = session.get(
        f"{REPO_API}/actions/workflows/{WORKFLOW_FILE}/runs",
        headers=dict(headers), params={"event": "workflow_dispatch", "per_page": 20}, timeout=30,
    )
    response.raise_for_status()
    artifact_name = f"room-fenced-recovery-{fence.recovery_id}"
    runs = _plausible_dispatched_runs(
        [value for value in response.json().get("workflow_runs", []) if isinstance(value, Mapping)],
        fence,
    )
    unavailable: list[GenerationUnavailable] = []
    for run in runs:
        status = str(run.get("status", ""))
        conclusion = str(run.get("conclusion") or "")
        if status != "completed":
            unavailable.append(GenerationUnavailable(
                str(run.get("id", "")), status, conclusion,
                ReasonCode.ACTIONS_RUN_DELAYED, False,
            ))
            continue
        artifacts = session.get(
            f"{REPO_API}/actions/runs/{run['id']}/artifacts",
            headers=dict(headers), params={"per_page": 100}, timeout=30,
        )
        artifacts.raise_for_status()
        artifact = next(
            (value for value in artifacts.json().get("artifacts", [])
             if value.get("name") == artifact_name and not value.get("expired")),
            None,
        )
        if artifact is None:
            actions_failed = conclusion != "success"
            terminal = actions_failed or _artifact_grace_exhausted(run, now)
            unavailable.append(GenerationUnavailable(
                str(run.get("id", "")), status, conclusion,
                ReasonCode.ACTIONS_RUN_FAILED if actions_failed else ReasonCode.ARTIFACT_DELAYED,
                terminal,
            ))
            continue
        archive = session.get(artifact["archive_download_url"], headers=dict(headers), timeout=60)
        archive.raise_for_status()
        reports = fence.directory / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
            for member in bundle.infolist():
                name = Path(member.filename)
                if member.is_dir() or name.is_absolute() or ".." in name.parts:
                    continue
                if name.name.startswith("room_") and name.suffix == ".json":
                    (reports / name.name).write_bytes(bundle.read(member))
        manifest = load_fenced_manifest(
            fence.manifest_path,
            expected_head_sha=fence.head_sha,
            expected_recovery_id=fence.recovery_id,
            expected_revision=fence.revision,
            state_store=store,
        )
        _validate_candidate_hashes(manifest)
        if str(manifest["routine_date_jst"]) != fence.routine_date:
            raise RuntimeError("fenced artifact belongs to a different JST routine date")
        if str(manifest["actions_run_id"]) != str(run["id"]):
            raise RuntimeError("manifest Actions run ID does not match its artifact owner")
        result = FencedArtifact(
            fence.manifest_path,
            str(run["id"]),
            status,
            conclusion,
        )
        partial = _is_partial_manifest(manifest, result)
        store.complete_recovery_control(
            fence.recovery_id, owner_id=fence.owner_id,
            replacement_run_id=str(run["id"]),
            result_status="partial" if partial else "ready",
        )
        _atomic_json(
            fence.control_path,
            {
                "status": "partial" if partial else "ready", "recovery_id": fence.recovery_id,
                "routine_date": fence.routine_date, "expected_head_sha": fence.head_sha,
                "revision": fence.revision, "replacement_run_id": str(run["id"]),
                "actions_status": status, "actions_conclusion": conclusion,
                "manifest_path": str(fence.manifest_path),
            },
        )
        _store_manifest_verification(fence, result, now=now)
        return result
    if len(runs) == 1 and unavailable:
        return unavailable[0]
    if unavailable:
        newest = unavailable[0]
        return GenerationUnavailable(
            newest.actions_run_id,
            newest.actions_status,
            newest.actions_conclusion,
            ReasonCode.ACTIONS_RUN_DELAYED,
            False,
        )
    return None


def record_generation_incidents(
    store: RoomStateStore,
    manifest: Mapping[str, Any],
    artifact: FencedArtifact,
    *,
    now: datetime,
) -> list[str]:
    """Record one stable incident per failed generation root and slot.

    A failed Actions run that already produced a blocked quality slot shares
    that root cause and therefore does not create a second Actions incident.
    The run id in the component keeps re-evaluation of the same artifact
    idempotent, including after a terminal incident transition.
    """
    day = str(manifest["routine_date_jst"])
    revision = int(manifest["revision"])
    component = f"generation_run:{artifact.actions_run_id}"
    incidents: list[str] = []
    for slot in POST_SLOTS:
        value = manifest["slots"][slot]
        if value["status"] != "blocked":
            continue
        manifest_reason = str(value.get("reason", ""))
        reason = (
            ReasonCode.QUALITY_POOL_EXHAUSTED
            if manifest_reason == "missing_quality_safe_candidate"
            else ReasonCode.COPY_VALIDATION_REGRESSION
        )
        incidents.append(
            store.create_incident(
                day,
                reason=reason,
                last_safe_state=SlotStatus.BLOCKED.value,
                next_action="review generation quality without reposting",
                slot=slot,
                component=component,
                manifest_revision=revision,
                dedupe_terminal=True,
                now=now,
            )
        )
    if artifact.actions_conclusion != "success" and not incidents:
        incidents.append(
            store.create_incident(
                day,
                reason=ReasonCode.ACTIONS_RUN_FAILED,
                last_safe_state="artifact_validated",
                next_action="inspect failed Actions run before further generation",
                component=component,
                manifest_revision=revision,
                dedupe_terminal=True,
                now=now,
            )
        )
    return incidents


def record_generation_unavailable(
    store: RoomStateStore,
    fence: GenerationFence,
    unavailable: GenerationUnavailable,
    *,
    now: datetime,
) -> str:
    if not unavailable.terminal:
        raise ValueError("a non-terminal generation state must not create an incident")
    store.fail_recovery_control(
        fence.recovery_id,
        owner_id=fence.owner_id,
        replacement_run_id=unavailable.actions_run_id,
        now=now,
    )
    return store.create_incident(
        fence.routine_date,
        reason=unavailable.reason,
        last_safe_state="generation_completed_without_valid_artifact",
        next_action="inspect the completed Actions run; do not post without a validated artifact",
        component=f"generation_run:{unavailable.actions_run_id}",
        manifest_revision=fence.revision,
        dedupe_terminal=True,
        now=now,
    )


def next_eligible_slot(
    manifest: Mapping[str, Any], store: RoomStateStore, now: datetime
) -> str | None:
    day = str(manifest["routine_date_jst"])
    if not posting_window_open(day, now):
        return None
    for slot in POST_SLOTS:
        due = {"morning": time(8), "noon": time(12), "evening": time(19)}[slot]
        if now.astimezone(JST).time().replace(tzinfo=None) < due:
            continue
        state = store.get_slot(day, slot)
        if state is not None and state.status in {
            # These states can still represent an external submit in flight.
            # Do not let another slot touch the same browser/account until
            # that operation is reconciled.
            SlotStatus.CLAIMED,
            SlotStatus.SUBMITTING,
            SlotStatus.SUBMITTED_UNCONFIRMED,
        }:
            raise RuntimeError(
                f"unresolved {state.status.value.upper()} blocks automatic continuation: {day}:{slot}"
            )
        # UNCERTAIN is terminal for this invocation: the submit boundary was
        # crossed, but the immediate verifier could not prove the ROOM card.
        # It is safe to continue with a different slot because this runner
        # never retries an UNCERTAIN slot automatically.  A later authenticated
        # reconciliation still decides POSTED vs confirmed_not_posted.
        if manifest["slots"][slot]["status"] == "ready" and state is not None and state.status in {
            SlotStatus.READY, SlotStatus.FAILED_PRE_SUBMIT,
        }:
            return slot
    return None


def audit_day(store: RoomStateStore, ledger_path: Path, routine_date: str, *, sync_ledger: bool = True) -> dict[str, Any]:
    store.quick_check()
    if sync_ledger:
        store.sync_legacy_ledger(ledger_path, routine_date=routine_date)
    snapshot = store.export_snapshot(routine_date)
    ledger_posted: dict[str, str] = {}
    if ledger_path.exists():
        for raw in ledger_path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                continue
            prefix = f"{routine_date}:"
            if value.get("status") == "posted" and str(value.get("post_slot", "")).startswith(prefix):
                ledger_posted[str(value["post_slot"]).split(":", 1)[1]] = str(value.get("normalized_url", ""))
    errors: list[str] = []
    with closing(store.connect()) as connection:
        for slot in POST_SLOTS:
            state = snapshot["slots"].get(slot)
            if state is None or state["status"] != SlotStatus.POSTED.value:
                errors.append(f"{slot}:db_not_posted")
                continue
            if ledger_posted.get(slot) != state["normalized_url"]:
                errors.append(f"{slot}:ledger_mismatch")
            attempt = connection.execute(
                """SELECT status, submit_started FROM post_attempts
                   WHERE routine_date = ? AND slot = ? ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                (routine_date, slot),
            ).fetchone()
            if attempt is None or attempt["status"] != SlotStatus.POSTED.value or not int(attempt["submit_started"]):
                errors.append(f"{slot}:attempt_mismatch")
    # ``budget_exhausted`` is a terminal audit record, not an active
    # incident.  It must remain in the snapshot for accountability but must
    # not poison later healthy-day audits after the bounded recovery budget
    # was deliberately consumed.  NEEDS_HUMAN and all in-flight states still
    # fail closed.
    open_incidents = [
        value for value in snapshot["incidents"]
        if value["status"] not in {"resolved", "budget_exhausted"}
    ]
    if open_incidents:
        errors.append("open_incidents")
    return {"routine_date": routine_date, "ok": not errors, "errors": errors}


def run(*, now: datetime, apply: bool, session: Any, replacement_id: str | None = None) -> dict[str, Any]:
    day = routine_date_jst(now).isoformat()
    sanitized_heartbeat(
        ORCHESTRATOR_HEARTBEAT_PATH,
        role="orchestrator",
        status="running",
        routine_date=day,
        now=now,
    )
    def finish(
        result: dict[str, Any], *, heartbeat_status: str | None = None
    ) -> dict[str, Any]:
        sanitized_heartbeat(
            ORCHESTRATOR_HEARTBEAT_PATH,
            role="orchestrator",
            status=heartbeat_status or str(result["status"]),
            routine_date=day,
            now=now,
        )
        return result

    try:
        store = RoomStateStore(DATABASE_PATH)
        store.initialize()
        store.import_legacy_ledger(LEDGER_PATH, now=now)
        store.expire_previous_days(day, now=now)
        fence = generation_fence(store, now, replacement_id)
        headers = github_headers(github_token())
        dispatched = dispatch_generation_once(
            session, headers=headers, store=store, fence=fence, now=now
        )
        artifact = fetch_fenced_artifact(
            session, headers=headers, store=store, fence=fence, now=now
        )
        if artifact is None:
            return finish({"status": "generation_pending", "dispatched": dispatched, "routine_date": day})
        if isinstance(artifact, GenerationUnavailable):
            if not artifact.terminal:
                return finish({
                    "status": "generation_pending",
                    "pending_reason": artifact.reason.value,
                    "actions_run_id": artifact.actions_run_id,
                    "dispatched": dispatched,
                    "routine_date": day,
                })
            incident_id = record_generation_unavailable(
                store, fence, artifact, now=now
            )
            return finish({
                "status": "generation_failed",
                "failure_reason": artifact.reason.value,
                "actions_run_id": artifact.actions_run_id,
                "incident_id": incident_id,
                "routine_date": day,
            }, heartbeat_status="failed")
        manifest = load_fenced_manifest(
            artifact.path, expected_head_sha=fence.head_sha,
            expected_recovery_id=fence.recovery_id, expected_revision=fence.revision,
            state_store=store,
        )
        store.accept_manifest(manifest, now=now)
        incident_ids = record_generation_incidents(store, manifest, artifact, now=now)
        partial = _is_partial_manifest(manifest, artifact)
        local_time = now.astimezone(JST).time().replace(tzinfo=None)
        if local_time >= AUDIT_TIME:
            audit = audit_day(store, LEDGER_PATH, day)
            return finish({"status": "audit_ok" if audit["ok"] else "audit_failed", **audit})
        slot = next_eligible_slot(manifest, store, now)
        if slot is None:
            return finish({
                "status": "partial" if partial else "no_eligible_slot",
                "routine_date": day,
                "incident_count": len(incident_ids),
            })
        result = execute(
            manifest=manifest, slot=slot, active_slots=set(POST_SLOTS), now=now,
            apply=apply, store=store, legacy_ledger_path=LEDGER_PATH,
        )
        return finish({
            "status": "partial" if partial else (result["status"] if apply else "dry_run"),
            "routine_date": day,
            "slot": slot,
            "slot_result": result["status"] if apply else "dry_run",
            "incident_count": len(incident_ids),
        })
    except Exception as exc:
        sanitized_heartbeat(
            ORCHESTRATOR_HEARTBEAT_PATH,
            role="orchestrator",
            status="failed",
            routine_date=day,
            now=now,
            error_code=type(exc).__name__,
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Unified Phase 8 ROOM orchestrator")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--replacement-id", default="")
    args = parser.parse_args()
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
    )
    try:
        import requests
        with requests.Session() as session:
            result = run(
                now=datetime.now(tz=JST), apply=args.apply, session=session,
                replacement_id=args.replacement_id or None,
            )
        logging.info("Phase 8 result: %s", json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] not in {
            "audit_failed", "generation_failed", "partial", SlotStatus.UNCERTAIN.value
        } else 3
    except Exception as exc:
        logging.error("Phase 8 stopped safely: %s", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
