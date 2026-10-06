from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from room_manifest_v2 import build_manifest_v2
from room_operation_contract import JST, POST_SLOTS
from room_orchestrator_shadow import audit_shadow_manifests, evaluate_shadow_manifest
from room_shadow_replay import replay_generation_report
from run_phase4_shadow import fetch_distinct_reports, github_headers, github_token


def run_replay(*, start: date, days: int, max_runs: int, output_dir: Path) -> dict[str, Any]:
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    with requests.Session() as session:
        observations, incompatible = fetch_distinct_reports(
            session,
            headers=github_headers(github_token()),
            target_days=days,
            max_runs=max_runs,
            observation_start=start,
        )

    manifests: list[dict[str, Any]] = []
    observations_summary: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for observation in sorted(observations, key=lambda value: value["day"]):
        day = date.fromisoformat(observation["day"])
        run = observation["run"]
        day_dir = output_dir / day.isoformat()
        replay_report = replay_generation_report(
            observation["report"],
            routine_date=day,
            source_actions_run_id=run["id"],
            output_dir=day_dir,
            head_sha=head_sha,
        )
        manifest = build_manifest_v2(
            replay_report,
            actions_run_id=f"shadow-replay-{run['id']}",
            head_sha=head_sha,
            revision=1,
            generated_at=datetime.combine(day, datetime.min.time().replace(hour=7, minute=30), tzinfo=JST),
        )
        manifests.append(manifest)
        (day_dir / "phase4_replay_manifest_v2.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        day_decisions = evaluate_shadow_manifest(
            manifest,
            now=datetime.combine(day, datetime.min.time().replace(hour=19), tzinfo=JST),
        )
        decisions.extend(value.as_dict() for value in day_decisions)
        observations_summary.append(
            {
                "routine_date_jst": day.isoformat(),
                "source_actions_run_id": str(run["id"]),
                "source_product_count": len(observation["report"].get("items", [])),
                "ready_slots": replay_report.get("ready_slots", []),
                "missing_post_slots": replay_report.get("missing_post_slots", []),
            }
        )

    quality = audit_shadow_manifests(manifests)
    required_decisions = len(manifests) * len(POST_SLOTS)
    would_claim = sum(value["action"] == "would_claim" for value in decisions)
    ready = (
        len(manifests) >= days
        and quality.passed
        and would_claim == required_decisions
    )
    summary = {
        "schema_version": 1,
        "mode": "phase3_improved_real_data_replay",
        "created_at": datetime.now(JST).isoformat(),
        "external_mutations": 0,
        "source_network_methods": ["GET"],
        "sheets_access": "none",
        "room_posting": "disabled",
        "target_business_days": days,
        "business_days_observed": len(manifests),
        "observations": observations_summary,
        "legacy_contract_incompatible_runs": incompatible,
        "decisions": decisions,
        "quality": quality.as_dict(),
        "ready_for_phase5": ready,
        "blocking_reasons": [
            reason
            for condition, reason in (
                (len(manifests) < days, f"only_{len(manifests)}_of_{days}_required_days_replayed"),
                (not quality.passed, "quality_gate_failed"),
                (would_claim != required_decisions, "one_or_more_slots_not_claimable"),
            )
            if condition
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "latest-replay-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Replay real ROOM product snapshots with Phase 3 code")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--max-runs", type=int, default=100)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "reports" / "phase4-replay",
    )
    args = parser.parse_args()
    summary = run_replay(
        start=args.start,
        days=args.days,
        max_runs=args.max_runs,
        output_dir=args.output_dir,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ready_for_phase5"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
