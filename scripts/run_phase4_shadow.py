from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import time as time_module
import zipfile
from collections import Counter
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Iterable, Mapping

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from room_manifest_v2 import build_manifest_v2
from room_operation_contract import JST, POST_SLOTS, SlotStatus, normalize_product_url
from room_orchestrator_shadow import audit_shadow_manifests, evaluate_shadow_manifest
from room_state_store import RoomStateStore


REPO_API = "https://api.github.com/repos/dai-kun811/rakuten-room-agent"
ARTIFACT_NAME = "room-generation-report"
DEFAULT_GIT = Path(
    r"C:\Users\daiku\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe"
)


def github_token() -> str:
    git = str(DEFAULT_GIT) if DEFAULT_GIT.exists() else "git"
    completed = subprocess.run(
        [git, "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        text=True,
        capture_output=True,
        check=True,
    )
    values = dict(
        line.split("=", 1)
        for line in completed.stdout.splitlines()
        if "=" in line
    )
    token = values.get("password", "")
    if not token:
        raise RuntimeError("GitHub credential is unavailable")
    return token


def github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Rakuten-ROOM-phase4-shadow",
    }


def _get_json(
    session: requests.Session,
    url: str,
    *,
    headers: Mapping[str, str],
    params: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    response = session.get(url, headers=dict(headers), params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise RuntimeError(f"unexpected GitHub response for {url}")
    return payload


def _download_report(
    session: requests.Session,
    run: Mapping[str, Any],
    *,
    headers: Mapping[str, str],
) -> Mapping[str, Any] | None:
    artifact = None
    for attempt in range(2):
        artifacts = _get_json(
            session,
            f"{REPO_API}/actions/runs/{run['id']}/artifacts",
            headers=headers,
        )
        artifact = next(
            (
                item
                for item in artifacts.get("artifacts", [])
                if item.get("name") == ARTIFACT_NAME and not item.get("expired")
            ),
            None,
        )
        if artifact is not None:
            break
        if attempt == 0:
            time_module.sleep(0.5)
    if artifact is None:
        return None
    response = session.get(
        str(artifact["archive_download_url"]),
        headers=dict(headers),
        timeout=30,
    )
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = [name for name in archive.namelist() if name.endswith(".json")]
        if not names:
            return None
        preferred = next(
            (name for name in names if "generation" in name.lower() and "report" in name.lower()),
            names[0],
        )
        payload = json.loads(archive.read(preferred).decode("utf-8-sig"))
    return payload if isinstance(payload, Mapping) else None


def _report_time(report: Mapping[str, Any], run: Mapping[str, Any]) -> datetime:
    raw = str(
        report.get("executed_at")
        or report.get("generated_at")
        or run.get("run_started_at")
        or run.get("created_at")
    )
    value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("report timestamp must include timezone")
    return value.astimezone(JST)


def _three_slot_compatible(report: Mapping[str, Any]) -> bool:
    required = report.get("required_post_slots")
    return isinstance(required, list) and tuple(required) == POST_SLOTS


def fetch_distinct_reports(
    session: requests.Session,
    *,
    headers: Mapping[str, str],
    target_days: int,
    max_runs: int,
    observation_start: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    runs_payload = _get_json(
        session,
        f"{REPO_API}/actions/workflows/daily.yml/runs",
        headers=headers,
        params={"status": "success", "per_page": min(max_runs, 100)},
    )
    runs = sorted(
        list(runs_payload.get("workflow_runs", [])),
        key=lambda value: str(value.get("created_at", "")),
        reverse=True,
    )[:max_runs]
    compatible_by_day: dict[str, dict[str, Any]] = {}
    incompatible: list[dict[str, Any]] = []
    for run in runs:
        report = _download_report(session, run, headers=headers)
        if report is None:
            continue
        generated_at = _report_time(report, run)
        day = generated_at.date().isoformat()
        if generated_at.date() < observation_start:
            # Runs are sorted newest first. Once their report day is older than
            # the observation window, no older run can contribute a fresh day.
            break
        observation = {
            "day": day,
            "run": run,
            "report": report,
            "generated_at": generated_at,
        }
        if not _three_slot_compatible(report):
            incompatible.append(
                {
                    "day": day,
                    "actions_run_id": str(run.get("id", "")),
                    "required_post_slots": report.get("required_post_slots", []),
                }
            )
            continue
        compatible_by_day.setdefault(day, observation)
        if len(compatible_by_day) >= target_days:
            break
    return list(compatible_by_day.values()), incompatible


def legacy_candidate_url(report: Mapping[str, Any], slot: str) -> str:
    candidates = [
        item
        for item in report.get("items", [])
        if isinstance(item, Mapping)
        and item.get("status") == "ready"
        and item.get("post_slot") == slot
        and item.get("product_url")
        and item.get("body")
    ]
    if not candidates:
        return ""
    return normalize_product_url(str(candidates[0]["product_url"]))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _populate_isolated_state(
    output_dir: Path,
    manifests: Iterable[Mapping[str, Any]],
    *,
    run_label: str,
) -> Path:
    path = output_dir / f"shadow-state-{run_label}.db"
    store = RoomStateStore(path)
    store.initialize()
    for manifest in manifests:
        day = str(manifest["routine_date_jst"])
        for slot in POST_SLOTS:
            value = manifest["slots"][slot]
            if value["status"] == "blocked":
                store.create_slot(
                    day,
                    slot,
                    status=SlotStatus.BLOCKED,
                    manifest_revision=int(manifest["revision"]),
                    source="phase4_shadow",
                )
                continue
            candidate = value["candidate"]
            store.create_slot(
                day,
                slot,
                status=SlotStatus.READY,
                manifest_revision=int(manifest["revision"]),
                normalized_url=str(candidate["normalized_url"]),
                content_hash=str(candidate["content_hash"]),
                product_type=str(candidate["product_type"]),
                source="phase4_shadow",
            )
    return path


def run_shadow(
    *,
    target_days: int,
    max_runs: int,
    output_dir: Path,
    observation_start: date,
) -> dict[str, Any]:
    token = github_token()
    headers = github_headers(token)
    with requests.Session() as session:
        observations, incompatible = fetch_distinct_reports(
            session,
            headers=headers,
            target_days=target_days,
            max_runs=max_runs,
            observation_start=observation_start,
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    manifests: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    differences: list[dict[str, Any]] = []
    source_runs: list[dict[str, Any]] = []
    for observation in observations:
        run = observation["run"]
        report = observation["report"]
        generated_at = observation["generated_at"]
        manifest = build_manifest_v2(
            report,
            actions_run_id=run["id"],
            head_sha=str(run.get("head_sha", "unknown")),
            revision=1,
            generated_at=generated_at,
        )
        manifests.append(manifest)
        source_runs.append(
            {
                "routine_date_jst": manifest["routine_date_jst"],
                "actions_run_id": str(run["id"]),
                "head_sha": str(run.get("head_sha", "")),
                "generated_at": generated_at.isoformat(),
            }
        )
        simulated_time = datetime.combine(generated_at.date(), time(19, 0), tzinfo=JST)
        for decision in evaluate_shadow_manifest(manifest, now=simulated_time):
            item = decision.as_dict()
            decisions.append(item)
            old_url = legacy_candidate_url(report, decision.slot)
            if decision.action.value == "would_claim" and decision.normalized_url != old_url:
                differences.append(
                    {
                        "routine_date_jst": decision.routine_date,
                        "slot": decision.slot,
                        "shadow_action": decision.action.value,
                        "shadow_url": decision.normalized_url,
                        "legacy_url": old_url,
                        "reason": "candidate_selection_differs",
                    }
                )
            elif old_url and decision.action.value in {"blocked", "human_hold"}:
                differences.append(
                    {
                        "routine_date_jst": decision.routine_date,
                        "slot": decision.slot,
                        "shadow_action": decision.action.value,
                        "shadow_url": decision.normalized_url,
                        "legacy_url": old_url,
                        "reason": "shadow_safety_gate_blocks_legacy_candidate",
                    }
                )
        _write_json(
            output_dir / f"manifest-v2-{manifest['routine_date_jst']}.json",
            manifest,
        )

    quality = audit_shadow_manifests(manifests)
    run_label = datetime.now(JST).strftime("%Y%m%dT%H%M%S%f%z")
    state_path = _populate_isolated_state(output_dir, manifests, run_label=run_label)
    action_counts = Counter(item["action"] for item in decisions)
    enough_days = len(manifests) >= target_days
    ready_for_phase5 = enough_days and quality.passed and not differences
    summary = {
        "schema_version": 1,
        "mode": "shadow_read_only",
        "created_at": datetime.now(JST).isoformat(),
        "external_mutations": 0,
        "network_methods_used": ["GET"],
        "target_observation_days": target_days,
        "observation_start_jst": observation_start.isoformat(),
        "compatible_days_observed": len(manifests),
        "source_runs": source_runs,
        "legacy_contract_incompatible_runs": incompatible,
        "decision_counts": dict(action_counts),
        "decisions": decisions,
        "legacy_differences": differences,
        "quality": quality.as_dict(),
        "isolated_state_path": str(state_path),
        "ready_for_phase5": ready_for_phase5,
        "blocking_reasons": [
            reason
            for condition, reason in (
                (not enough_days, f"only_{len(manifests)}_of_{target_days}_required_days_observed"),
                (not quality.passed, "quality_gate_failed"),
                (bool(differences), "legacy_decision_difference_detected"),
            )
            if condition
        ],
    }
    _write_json(output_dir / "latest-summary.json", summary)
    return summary


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Read-only Phase 4 orchestrator shadow verification",
    )
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--max-runs", type=int, default=40)
    parser.add_argument(
        "--observation-start",
        type=date.fromisoformat,
        default=datetime.now(JST).date(),
        help="Count only real reports generated on or after this JST date",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "reports" / "phase4-shadow",
    )
    args = parser.parse_args()
    if args.days < 1 or args.max_runs < 1:
        parser.error("--days and --max-runs must be positive")
    summary = run_shadow(
        target_days=args.days,
        max_runs=args.max_runs,
        output_dir=args.output_dir,
        observation_start=args.observation_start,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ready_for_phase5"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
