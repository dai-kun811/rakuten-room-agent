from __future__ import annotations

import argparse
import io
import json
import logging
import os
import subprocess
import uuid
import zipfile
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any, Mapping

from local_room_worker import REPO_API, github_headers, github_token
from room_operation_contract import JST, POST_SLOTS, SlotStatus, posting_window_open, routine_date_jst
from room_orchestrator_worker import DATABASE_PATH, LEDGER_PATH, execute, load_fenced_manifest
from room_state_store import RoomStateStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = PROJECT_ROOT / ".local" / "room-worker"
RECOVERY_ROOT = STATE_ROOT / "generation-recoveries"
LOG_PATH = STATE_ROOT / "phase8-orchestrator.log"
WORKFLOW_FILE = "room-fenced-recovery.yml"
OLD_FENCED_RUN_ID = "37579267266"
AUDIT_TIME = time(20, 30)


@dataclass(frozen=True)
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
) -> Path | None:
    if fence.manifest_path.exists():
        load_fenced_manifest(
            fence.manifest_path,
            expected_head_sha=fence.head_sha,
            expected_recovery_id=fence.recovery_id,
            expected_revision=fence.revision,
            state_store=store,
        )
        return fence.manifest_path
    response = session.get(
        f"{REPO_API}/actions/workflows/{WORKFLOW_FILE}/runs",
        headers=dict(headers), params={"event": "workflow_dispatch", "per_page": 20}, timeout=30,
    )
    response.raise_for_status()
    artifact_name = f"room-fenced-recovery-{fence.recovery_id}"
    for run in response.json().get("workflow_runs", []):
        if str(run.get("head_sha", "")) != fence.head_sha:
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
        if str(manifest["routine_date_jst"]) != fence.routine_date:
            raise RuntimeError("fenced artifact belongs to a different JST routine date")
        if str(manifest["actions_run_id"]) != str(run["id"]):
            raise RuntimeError("manifest Actions run ID does not match its artifact owner")
        store.complete_recovery_control(
            fence.recovery_id, owner_id=fence.owner_id,
            replacement_run_id=str(run["id"]),
        )
        _atomic_json(
            fence.control_path,
            {
                "status": "ready", "recovery_id": fence.recovery_id,
                "routine_date": fence.routine_date, "expected_head_sha": fence.head_sha,
                "revision": fence.revision, "replacement_run_id": str(run["id"]),
                "manifest_path": str(fence.manifest_path),
            },
        )
        return fence.manifest_path
    return None


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
            SlotStatus.CLAIMED,
            SlotStatus.SUBMITTING,
            SlotStatus.SUBMITTED_UNCONFIRMED,
            SlotStatus.UNCERTAIN,
        }:
            raise RuntimeError(
                f"unresolved {state.status.value.upper()} blocks automatic continuation: {day}:{slot}"
            )
        if manifest["slots"][slot]["status"] == "ready" and state is not None and state.status in {
            SlotStatus.READY, SlotStatus.FAILED_PRE_SUBMIT,
        }:
            return slot
    return None


def audit_day(store: RoomStateStore, ledger_path: Path, routine_date: str) -> dict[str, Any]:
    store.quick_check()
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
    open_incidents = [
        value for value in snapshot["incidents"]
        if value["status"] not in {"resolved"}
    ]
    if open_incidents:
        errors.append("open_incidents")
    return {"routine_date": routine_date, "ok": not errors, "errors": errors}


def run(*, now: datetime, apply: bool, session: Any, replacement_id: str | None = None) -> dict[str, Any]:
    store = RoomStateStore(DATABASE_PATH)
    store.initialize()
    store.import_legacy_ledger(LEDGER_PATH, now=now)
    day = routine_date_jst(now).isoformat()
    store.expire_previous_days(day, now=now)
    fence = generation_fence(store, now, replacement_id)
    headers = github_headers(github_token())
    dispatched = dispatch_generation_once(
        session, headers=headers, store=store, fence=fence, now=now
    )
    manifest_path = fetch_fenced_artifact(
        session, headers=headers, store=store, fence=fence
    )
    if manifest_path is None:
        return {"status": "generation_pending", "dispatched": dispatched, "routine_date": day}
    manifest = load_fenced_manifest(
        manifest_path, expected_head_sha=fence.head_sha,
        expected_recovery_id=fence.recovery_id, expected_revision=fence.revision,
        state_store=store,
    )
    store.accept_manifest(manifest, now=now)
    local_time = now.astimezone(JST).time().replace(tzinfo=None)
    if local_time >= AUDIT_TIME:
        audit = audit_day(store, LEDGER_PATH, day)
        return {"status": "audit_ok" if audit["ok"] else "audit_failed", **audit}
    slot = next_eligible_slot(manifest, store, now)
    if slot is None:
        return {"status": "no_eligible_slot", "routine_date": day}
    result = execute(
        manifest=manifest, slot=slot, active_slots=set(POST_SLOTS), now=now,
        apply=apply, store=store, legacy_ledger_path=LEDGER_PATH,
    )
    return {"status": result["status"] if apply else "dry_run", "routine_date": day, "slot": slot}


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
        return 0 if result["status"] not in {"audit_failed", SlotStatus.UNCERTAIN.value} else 3
    except Exception as exc:
        logging.error("Phase 8 stopped safely: %s", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
