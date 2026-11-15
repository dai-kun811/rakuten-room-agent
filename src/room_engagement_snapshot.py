"""Create a stable evidence snapshot without opening the live SQLite database."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DIR = PROJECT_ROOT / ".local" / "room-worker"
DEFAULT_OBSERVATION_ROOT = PROJECT_ROOT / ".local" / "room-a-observations"
EVIDENCE_FILES = (
    "operations.db",
    "engagement-ledger.jsonl",
    "daily-routine-summary.json",
    "engagement-heartbeat.json",
    "engagement-worker.log",
)


class SnapshotError(RuntimeError):
    """Raised before a snapshot can safely be created."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": _sha256(path),
    }


def _default_output_dir(now: datetime | None = None) -> Path:
    local = (now or datetime.now().astimezone()).astimezone()
    return DEFAULT_OBSERVATION_ROOT / f"{local:%Y%m%d-%H%M%S}-new15-evidence"


def create_snapshot(
    *,
    source_dir: Path = DEFAULT_SOURCE_DIR,
    output_dir: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Copy the five engagement evidence files and prove source stability."""
    source_dir = source_dir.resolve()
    output_dir = (output_dir or _default_output_dir(now)).resolve()
    if output_dir.exists():
        raise SnapshotError("snapshot_output_already_exists")

    wal_path = source_dir / "operations.db-wal"
    if wal_path.exists():
        raise SnapshotError("database_wal_present")

    sources = {name: source_dir / name for name in EVIDENCE_FILES}
    missing = [name for name, path in sources.items() if not path.is_file()]
    if missing:
        raise SnapshotError("snapshot_source_missing:" + ",".join(sorted(missing)))

    before = {name: _fingerprint(path) for name, path in sources.items()}
    output_dir.mkdir(parents=True, exist_ok=False)
    for name, source in sources.items():
        shutil.copy2(source, output_dir / name)
    after = {name: _fingerprint(path) for name, path in sources.items()}
    copied = {name: _fingerprint(output_dir / name) for name in EVIDENCE_FILES}

    files: dict[str, Any] = {}
    consistent = True
    for name in EVIDENCE_FILES:
        unchanged = before[name] == after[name]
        copy_matches = (
            before[name]["size"] == copied[name]["size"]
            and before[name]["sha256"] == copied[name]["sha256"]
        )
        file_consistent = unchanged and copy_matches
        consistent = consistent and file_consistent
        files[name] = {
            "consistent": file_consistent,
            "source_before": before[name],
            "source_after": after[name],
            "copy": copied[name],
        }

    created_at = (now or datetime.now().astimezone()).astimezone().isoformat()
    manifest = {
        "schema_version": 1,
        "created_at": created_at,
        "source_database_opened": False,
        "wal_absent": True,
        "consistent": consistent,
        "output_dir": str(output_dir),
        "files": files,
    }
    manifest_path = output_dir / "snapshot-manifest.json"
    temporary_manifest = output_dir / "snapshot-manifest.json.tmp"
    temporary_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary_manifest.replace(manifest_path)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy stable ROOM engagement evidence without opening live operations.db."
    )
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    try:
        manifest = create_snapshot(
            source_dir=args.source_dir,
            output_dir=args.output_dir,
        )
    except (OSError, SnapshotError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "consistent": False,
                    "error": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1

    report = {
        "ok": bool(manifest["consistent"]),
        "consistent": bool(manifest["consistent"]),
        "output_dir": manifest["output_dir"],
        "source_database_opened": False,
        "wal_absent": True,
        "file_count": len(manifest["files"]),
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
