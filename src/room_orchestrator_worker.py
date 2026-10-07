from __future__ import annotations

import argparse
import io
import json
import logging
import os
import zipfile
from datetime import datetime, time
from pathlib import Path
from typing import Any, Mapping

from local_room_worker import REPO_API, github_headers, github_token
from room_live_gateway import AuthenticatedRoomVerifier, RoomLiveGateway
from room_manifest_v2 import MANIFEST_BASENAME
from room_operation_contract import JST, POST_SLOTS, SlotStatus, routine_date_jst, validate_manifest_v2
from room_orchestrator import RoomOrchestrator
from room_poster import RoomPoster
from room_profile_lock import RoomProfileLockTimeout, room_profile_lock
from room_state_store import RoomStateStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = PROJECT_ROOT / ".local" / "room-worker"
PROFILE_DIR = STATE_DIR / "chrome-profile"
LEDGER_PATH = STATE_DIR / "post-ledger.jsonl"
DATABASE_PATH = STATE_DIR / "operations.db"
ORCHESTRATOR_LOCK_PATH = STATE_DIR / "orchestrator.lock"
PROFILE_LOCK_PATH = STATE_DIR / "chrome-profile.lock"
LOG_PATH = STATE_DIR / "orchestrator.log"
SLOT_DUE_TIMES = {"morning": time(8, 0), "noon": time(12, 0), "evening": time(19, 0)}


def fetch_latest_manifest(
    session: Any,
    *,
    headers: Mapping[str, str],
    now: datetime,
    expected_head_sha: str | None = None,
    expected_run_id: str | None = None,
    expected_recovery_id: str | None = None,
    workflow_file: str = "daily.yml",
) -> tuple[dict[str, Any], dict[str, Any]]:
    response = session.get(
        f"{REPO_API}/actions/workflows/{workflow_file}/runs",
        headers=dict(headers),
        params={"status": "success", "per_page": 20},
        timeout=30,
    )
    response.raise_for_status()
    day = routine_date_jst(now).isoformat()
    for run in response.json().get("workflow_runs", []):
        if expected_run_id and str(run.get("id", "")) != expected_run_id:
            continue
        if expected_head_sha and str(run.get("head_sha", "")) != expected_head_sha:
            continue
        artifacts = session.get(
            f"{REPO_API}/actions/runs/{run['id']}/artifacts",
            headers=dict(headers),
            params={"per_page": 100},
            timeout=30,
        )
        artifacts.raise_for_status()
        artifact = next(
            (
                item
                for item in artifacts.json().get("artifacts", [])
                if item.get("name") == "room-generation-report" and not item.get("expired")
            ),
            None,
        )
        if artifact is None:
            continue
        archive = session.get(
            artifact["archive_download_url"], headers=dict(headers), timeout=60
        )
        archive.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
            name = next(
                (
                    value
                    for value in bundle.namelist()
                    if value.endswith(f"{MANIFEST_BASENAME}.json")
                ),
                None,
            )
            if name is None:
                continue
            manifest = json.loads(bundle.read(name).decode("utf-8"))
        validate_manifest_v2(manifest)
        if str(manifest["routine_date_jst"]) != day:
            continue
        if str(manifest["actions_run_id"]) != str(run["id"]):
            continue
        if str(manifest["head_sha"]) != str(run.get("head_sha", "")):
            continue
        if expected_recovery_id and str(manifest.get("recovery_id", "")) != expected_recovery_id:
            continue
        return run, manifest
    raise RuntimeError("today's validated manifest v2 was not found")


def load_fenced_manifest(
    path: Path,
    *,
    expected_head_sha: str,
    expected_recovery_id: str,
    state_store: RoomStateStore | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    validate_manifest_v2(manifest)
    if str(manifest.get("head_sha", "")) != expected_head_sha:
        raise RuntimeError("local recovery manifest HEAD does not match the fence")
    if str(manifest.get("recovery_id", "")) != expected_recovery_id:
        raise RuntimeError("local recovery manifest recovery_id does not match the fence")
    if str(manifest.get("generation_channel", "")) != "local_fenced_recovery":
        raise RuntimeError("local recovery manifest has an invalid generation channel")
    if expected_revision is not None and int(manifest.get("revision", 0)) != expected_revision:
        raise RuntimeError("local recovery manifest revision does not match the fence")
    if state_store is not None:
        state_store.assert_recovery_control(
            expected_recovery_id,
            expected_head_sha=expected_head_sha,
            revision=int(manifest["revision"]),
        )
    return manifest


def slot_is_due(slot: str, now: datetime) -> bool:
    local = now.astimezone(JST)
    return local.time().replace(tzinfo=None) >= SLOT_DUE_TIMES[slot]


def safe_summary(manifest: Mapping[str, Any], active_slots: set[str], now: datetime) -> dict[str, Any]:
    slots = {}
    for slot in POST_SLOTS:
        value = manifest["slots"][slot]
        slots[slot] = {
            "active": slot in active_slots,
            "due": slot_is_due(slot, now),
            "manifest_status": value["status"],
            "product_type": str((value.get("candidate") or {}).get("product_type", "")),
        }
    return {
        "routine_date_jst": manifest["routine_date_jst"],
        "revision": manifest["revision"],
        "head_sha": manifest["head_sha"],
        "slots": slots,
    }


def execute(
    *,
    manifest: Mapping[str, Any],
    slot: str,
    active_slots: set[str],
    now: datetime,
    apply: bool,
    store: RoomStateStore,
    gateway: Any | None = None,
    legacy_ledger_path: Path = LEDGER_PATH,
) -> dict[str, Any]:
    validate_manifest_v2(manifest)
    if slot not in active_slots:
        raise RuntimeError(f"slot is not owned by the new orchestrator: {slot}")
    if not slot_is_due(slot, now):
        raise RuntimeError(f"slot is not due yet: {slot}")
    value = manifest["slots"][slot]
    if value["status"] != "ready":
        raise RuntimeError(f"manifest slot is blocked: {slot}")
    if not apply:
        return {"mode": "dry-run", "slot": slot, "would_execute": True}

    store.initialize()
    store.import_legacy_ledger(legacy_ledger_path, now=now)
    store.expire_previous_days(str(manifest["routine_date_jst"]), now=now)
    store.accept_manifest(manifest, now=now)
    candidate = value["candidate"]
    orchestrator = RoomOrchestrator(
        store,
        gateway
        or RoomLiveGateway(
            RoomPoster(user_data_dir=PROFILE_DIR, headless=True),
            AuthenticatedRoomVerifier(user_data_dir=PROFILE_DIR, headless=True),
        ),
        legacy_ledger_path=legacy_ledger_path,
    )
    result = orchestrator.execute_slot(
        str(manifest["routine_date_jst"]), slot, candidate, now=now
    )
    return {
        "mode": "apply",
        "slot": slot,
        "status": result.status.value,
        "attempt_id": result.attempt_id,
        "incident_id": result.incident_id,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ROOM state-owned orchestrator worker")
    parser.add_argument("--slot", choices=POST_SLOTS, required=True)
    parser.add_argument("--active-slots", required=True, help="comma-separated canary ownership")
    parser.add_argument("--apply", action="store_true", help="allow one external ROOM submission")
    parser.add_argument("--expected-head-sha", default="")
    parser.add_argument("--expected-run-id", default="")
    parser.add_argument("--expected-recovery-id", default="")
    parser.add_argument("--manifest-path", default="")
    parser.add_argument("--recovery-db", default="")
    parser.add_argument("--expected-revision", type=int, default=0)
    return parser.parse_args()


def configure_logging() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
    )


def main() -> int:
    args = parse_args()
    configure_logging()
    logger = logging.getLogger("room-orchestrator")
    active_slots = {value.strip() for value in args.active_slots.split(",") if value.strip()}
    if not active_slots or not active_slots.issubset(set(POST_SLOTS)):
        logger.error("Invalid active slot set.")
        return 2
    now = datetime.now(tz=JST)
    try:
        if args.manifest_path:
            if not args.expected_head_sha or not args.expected_recovery_id:
                raise RuntimeError("local manifest requires HEAD and recovery ID fences")
            recovery_store = RoomStateStore(args.recovery_db) if args.recovery_db else None
            manifest = load_fenced_manifest(
                Path(args.manifest_path),
                expected_head_sha=args.expected_head_sha,
                expected_recovery_id=args.expected_recovery_id,
                state_store=recovery_store,
                expected_revision=args.expected_revision or None,
            )
        else:
            import requests

            with requests.Session() as session:
                _, manifest = fetch_latest_manifest(
                    session,
                    headers=github_headers(github_token()),
                    now=now,
                    expected_head_sha=args.expected_head_sha or None,
                    expected_run_id=args.expected_run_id or None,
                    expected_recovery_id=args.expected_recovery_id or None,
                )
        logger.info("Manifest preflight: %s", json.dumps(safe_summary(manifest, active_slots, now)))
        if not args.apply:
            logger.info("Dry-run complete; no ROOM mutation was attempted.")
            return 0
        if not PROFILE_DIR.exists():
            raise RuntimeError("ROOM browser profile is missing")
        store = RoomStateStore(DATABASE_PATH)
        with room_profile_lock(ORCHESTRATOR_LOCK_PATH, timeout_seconds=0):
            with room_profile_lock(PROFILE_LOCK_PATH, timeout_seconds=0):
                result = execute(
                    manifest=manifest,
                    slot=args.slot,
                    active_slots=active_slots,
                    now=now,
                    apply=True,
                    store=store,
                )
        logger.info("Execution result: %s", json.dumps(result))
        return 0 if result["status"] == SlotStatus.POSTED.value else 3
    except RoomProfileLockTimeout:
        logger.error("ROOM execution lock is busy; no submission was attempted.")
        return 4
    except Exception as exc:
        logger.error("Orchestrator stopped safely: %s", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
