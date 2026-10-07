from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = PROJECT_ROOT / ".local" / "room-worker" / "generation-recoveries"


def _load_local_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _atomic_write(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one locally fenced, artifact-only ROOM generation")
    parser.add_argument("--recovery-id", required=True)
    parser.add_argument("--expected-head-sha", required=True)
    parser.add_argument("--revision", type=int, required=True)
    args = parser.parse_args()
    if args.revision < 1:
        raise RuntimeError("revision must be positive")
    head = _git_head()
    if head != args.expected_head_sha:
        raise RuntimeError("current HEAD does not match the recovery fence")

    recovery_dir = STATE_ROOT / args.recovery_id
    recovery_dir.mkdir(parents=True, exist_ok=True)
    control_path = recovery_dir / "control.json"
    if control_path.exists():
        raise RuntimeError("recovery ID was already consumed")
    started_at = datetime.now().astimezone().isoformat()
    owner_id = uuid.uuid4().hex
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    from room_state_store import RoomStateStore

    state_store = RoomStateStore(PROJECT_ROOT / ".local" / "room-worker" / "operations.db")
    state_store.reserve_recovery_control(
        recovery_id=args.recovery_id,
        routine_date=datetime.now().astimezone().date(),
        old_run_id="37579267266",
        expected_head_sha=head,
        revision=args.revision,
        owner_id=owner_id,
    )
    with control_path.open("x", encoding="utf-8") as handle:
        json.dump(
            {"recovery_id": args.recovery_id, "expected_head_sha": head, "revision": args.revision, "status": "running", "started_at": started_at},
            handle,
            ensure_ascii=False,
            indent=2,
        )
        handle.write("\n")

    _load_local_env(PROJECT_ROOT / ".env")
    os.environ.update(
        {
            "ROOM_SHADOW_MODE": "true",
            "ROOM_RECOVERY_ID": args.recovery_id,
            "ROOM_GENERATION_CHANNEL": "local_fenced_recovery",
            "ROOM_MANIFEST_REVISION": str(args.revision),
            "GITHUB_RUN_ID": f"local-{args.recovery_id}",
            "GITHUB_SHA": head,
            "GITHUB_EVENT_NAME": "workflow_dispatch",
        }
    )
    try:
        sys.path.insert(0, str(PROJECT_ROOT / "src"))
        from main import main as generate

        with tempfile.TemporaryDirectory(prefix="room-fenced-generation-") as scratch:
            previous_cwd = Path.cwd()
            os.chdir(scratch)
            try:
                code = generate()
            finally:
                os.chdir(previous_cwd)
            if code != 0:
                raise RuntimeError(f"generation failed with exit code {code}")
            reports_dir = Path(scratch) / "reports"
            if not reports_dir.exists():
                raise RuntimeError("generation did not produce a reports directory")
            destination = recovery_dir / "reports"
            if destination.exists():
                raise RuntimeError("recovery reports already exist")
            shutil.copytree(reports_dir, destination)
        manifest_path = destination / "room_operational_manifest_v2.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("recovery_id") != args.recovery_id or manifest.get("head_sha") != head:
            raise RuntimeError("generated manifest did not preserve recovery fences")
        _atomic_write(
            control_path,
            {
                "recovery_id": args.recovery_id,
                "expected_head_sha": head,
                "revision": args.revision,
                "status": "ready",
                "started_at": started_at,
                "completed_at": datetime.now().astimezone().isoformat(),
                "manifest_path": str(manifest_path),
            },
        )
        state_store.complete_recovery_control(args.recovery_id, owner_id=owner_id)
        print(json.dumps({"status": "ready", "recovery_id": args.recovery_id, "revision": args.revision}))
        return 0
    except Exception:
        _atomic_write(
            control_path,
            {
                "recovery_id": args.recovery_id,
                "expected_head_sha": head,
                "revision": args.revision,
                "status": "failed",
                "started_at": started_at,
                "completed_at": datetime.now().astimezone().isoformat(),
            },
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
