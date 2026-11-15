"""Read-only verification for the paired daily ROOM engagement routine.

The production SQLite database must be copied before this verifier is run.  The
CLI deliberately refuses to open the canonical live database, and opens the
copy with SQLite ``mode=ro``, ``immutable=1``, and ``query_only`` enabled.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LIVE_DATABASE_PATH = PROJECT_ROOT / ".local" / "room-worker" / "operations.db"
EXPECTED_POLICY_ID = "new15_follow_and_like_latest_v1"
EXPECTED_GOAL = 15
PAIR_LIKE_STATUSES = {"liked", "already_liked_paired_post"}
PAIR_FOLLOW_STATUSES = {"followed", "paired_follow_already_recorded"}


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return payload


def _read_ledger(path: Path, routine_date: str) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    malformed = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(payload, dict) and payload.get("routine_date") == routine_date:
            events.append(payload)
    return events, malformed


def _connect_read_only(path: Path) -> sqlite3.Connection:
    resolved = path.resolve()
    uri_path = quote(resolved.as_posix(), safe="/:")
    connection = sqlite3.connect(
        f"file:{uri_path}?mode=ro&immutable=1",
        uri=True,
        timeout=5,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _append_mismatch(errors: list[str], condition: bool, code: str) -> None:
    if condition:
        errors.append(code)


def verify_engagement(
    *,
    database_path: Path,
    ledger_path: Path,
    summary_path: Path,
    heartbeat_path: Path,
    log_path: Path,
    routine_date: str,
    goal: int = EXPECTED_GOAL,
    policy_id: str = EXPECTED_POLICY_ID,
    require_clean_log: bool = False,
) -> dict[str, Any]:
    """Verify DB, ledger, summary, heartbeat, and log evidence for one day."""
    date.fromisoformat(routine_date)
    errors: list[str] = []
    warnings: list[str] = []

    required_paths = {
        "database": database_path,
        "ledger": ledger_path,
        "summary": summary_path,
        "heartbeat": heartbeat_path,
        "log": log_path,
    }
    for label, path in required_paths.items():
        if not path.is_file():
            errors.append(f"{label}_missing")
    if errors:
        return {
            "routine_date": routine_date,
            "policy_id": policy_id,
            "goal": goal,
            "ok": False,
            "clean_run": False,
            "errors": errors,
            "warnings": warnings,
        }

    with closing(_connect_read_only(database_path)) as connection:
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        run = connection.execute(
            """SELECT routine_date,status,goal,followed,liked,owner_id,last_error_code
               FROM engagement_runs WHERE routine_date=?""",
            (routine_date,),
        ).fetchone()
        actions = connection.execute(
            """SELECT candidate_id,room_url,followed,liked,follow_status,like_status
               FROM engagement_actions WHERE routine_date=? ORDER BY candidate_id""",
            (routine_date,),
        ).fetchall()
        duplicate_action_keys = int(
            connection.execute(
                """SELECT COUNT(*) FROM (
                       SELECT candidate_id FROM engagement_actions
                       WHERE routine_date=? GROUP BY candidate_id HAVING COUNT(*) > 1
                   )""",
                (routine_date,),
            ).fetchone()[0]
        )

    _append_mismatch(errors, quick_check != "ok", "database_quick_check_failed")
    if run is None:
        errors.append("engagement_run_missing")
        run_data: dict[str, Any] = {}
    else:
        run_data = dict(run)
        _append_mismatch(errors, run["status"] != "completed", "run_not_completed")
        _append_mismatch(errors, int(run["goal"]) != goal, "run_goal_mismatch")
        _append_mismatch(errors, int(run["followed"]) != goal, "run_followed_mismatch")
        _append_mismatch(errors, int(run["liked"]) != goal, "run_liked_mismatch")
        _append_mismatch(errors, run["owner_id"] is not None, "run_owner_not_released")
        _append_mismatch(errors, bool(run["last_error_code"]), "run_error_present")

    followed_ids = {str(row["candidate_id"]) for row in actions if bool(row["followed"])}
    liked_ids = {str(row["candidate_id"]) for row in actions if bool(row["liked"])}
    paired_ids = followed_ids & liked_ids
    _append_mismatch(errors, len(followed_ids) != goal, "db_new_follow_count_mismatch")
    _append_mismatch(errors, len(liked_ids) != goal, "db_like_count_mismatch")
    _append_mismatch(errors, followed_ids != liked_ids, "db_pair_set_mismatch")
    _append_mismatch(errors, len(paired_ids) != goal, "db_pair_count_mismatch")
    _append_mismatch(errors, duplicate_action_keys != 0, "db_duplicate_action_keys")
    _append_mismatch(
        errors,
        any(
            row["follow_status"] == "already_following"
            and (bool(row["followed"]) or bool(row["liked"]))
            for row in actions
        ),
        "already_following_counted",
    )
    _append_mismatch(
        errors,
        any(
            str(row["candidate_id"]) in paired_ids
            and str(row["follow_status"]) not in PAIR_FOLLOW_STATUSES
            for row in actions
        ),
        "db_pair_follow_status_invalid",
    )
    _append_mismatch(
        errors,
        any(
            str(row["candidate_id"]) in paired_ids
            and str(row["like_status"]) not in PAIR_LIKE_STATUSES
            for row in actions
        ),
        "db_pair_like_status_invalid",
    )

    ledger_events, malformed_lines = _read_ledger(ledger_path, routine_date)
    _append_mismatch(errors, malformed_lines != 0, "ledger_malformed_json")
    ledger_progress: dict[str, dict[str, Any]] = {}
    for event in ledger_events:
        candidate_id = str(event.get("candidate_id", "")).strip()
        if not candidate_id:
            errors.append("ledger_candidate_id_missing")
            continue
        item = ledger_progress.setdefault(
            candidate_id,
            {
                "followed": False,
                "liked": False,
                "new_follow_evidence": False,
                "paired_like_evidence": False,
                "room_urls": set(),
            },
        )
        item["followed"] = item["followed"] or bool(event.get("followed"))
        item["liked"] = item["liked"] or bool(event.get("liked"))
        item["new_follow_evidence"] = item["new_follow_evidence"] or (
            bool(event.get("followed")) and event.get("follow_status") == "followed"
        )
        item["paired_like_evidence"] = item["paired_like_evidence"] or (
            bool(event.get("liked")) and event.get("like_status") in PAIR_LIKE_STATUSES
        )
        room_url = str(event.get("room_url", "")).strip()
        if room_url:
            item["room_urls"].add(room_url)

    ledger_followed_ids = {
        candidate_id for candidate_id, item in ledger_progress.items() if item["followed"]
    }
    ledger_liked_ids = {
        candidate_id for candidate_id, item in ledger_progress.items() if item["liked"]
    }
    new_follow_evidence_ids = {
        candidate_id
        for candidate_id, item in ledger_progress.items()
        if item["new_follow_evidence"]
    }
    paired_like_evidence_ids = {
        candidate_id
        for candidate_id, item in ledger_progress.items()
        if item["paired_like_evidence"]
    }
    _append_mismatch(errors, ledger_followed_ids != followed_ids, "ledger_follow_projection_mismatch")
    _append_mismatch(errors, ledger_liked_ids != liked_ids, "ledger_like_projection_mismatch")
    _append_mismatch(errors, new_follow_evidence_ids != followed_ids, "ledger_new_follow_evidence_mismatch")
    _append_mismatch(errors, paired_like_evidence_ids != liked_ids, "ledger_paired_like_evidence_mismatch")

    database_urls = {str(row["candidate_id"]): str(row["room_url"]) for row in actions}
    _append_mismatch(
        errors,
        any(
            len(item["room_urls"]) != 1
            or database_urls.get(candidate_id) not in item["room_urls"]
            for candidate_id, item in ledger_progress.items()
        ),
        "ledger_room_url_mismatch",
    )

    summary = _read_json(summary_path)
    summary_checks = {
        "summary_date_mismatch": summary.get("routine_date") != routine_date,
        "summary_policy_mismatch": summary.get("policy_id") != policy_id,
        "summary_goal_mismatch": summary.get("goal") != goal,
        "summary_followed_mismatch": summary.get("followed") != goal,
        "summary_liked_mismatch": summary.get("liked") != goal,
        "summary_not_completed": summary.get("completed") is not True,
        "summary_status_mismatch": summary.get("status") not in {"completed", "already_complete"},
        "summary_failures_present": summary.get("failures") != 0,
        "summary_error_present": bool(summary.get("error_code")),
        "summary_not_published": summary.get("published") is not True,
    }
    for code, mismatch in summary_checks.items():
        _append_mismatch(errors, mismatch, code)

    heartbeat = _read_json(heartbeat_path)
    heartbeat_checks = {
        "heartbeat_date_mismatch": heartbeat.get("routine_date") != routine_date,
        "heartbeat_role_mismatch": heartbeat.get("role") != "engagement",
        "heartbeat_not_completed": heartbeat.get("status") != "completed",
        "heartbeat_error_present": bool(heartbeat.get("error_code")),
    }
    for code, mismatch in heartbeat_checks.items():
        _append_mismatch(errors, mismatch, code)

    day_lines = [
        line
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.startswith(f"{routine_date} ")
    ]
    completion_pattern = re.compile(
        rf"(?:Engagement progress .*follow={goal}/{goal} like={goal}/{goal}|"
        rf"Daily engagement goal already complete follow={goal} like={goal})"
    )
    log_completion_present = any(completion_pattern.search(line) for line in day_lines)
    log_publish_present = any(
        f"Public routine progress updated day={routine_date} follow={goal} like={goal} completed=True"
        in line
        for line in day_lines
    )
    log_error_count = sum(" ERROR " in line for line in day_lines)
    _append_mismatch(errors, not log_completion_present, "log_completion_missing")
    _append_mismatch(errors, not log_publish_present, "log_publish_confirmation_missing")
    if log_error_count:
        warnings.append("log_errors_present")
        _append_mismatch(errors, require_clean_log, "clean_log_required")

    clean_run = not errors and log_error_count == 0
    report = {
        "routine_date": routine_date,
        "policy_id": policy_id,
        "goal": goal,
        "ok": not errors,
        "clean_run": clean_run,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "database": {
            "quick_check": quick_check,
            "run_status": run_data.get("status"),
            "run_goal": run_data.get("goal"),
            "run_followed": run_data.get("followed"),
            "run_liked": run_data.get("liked"),
            "owner_released": run_data.get("owner_id") is None if run_data else False,
            "action_count": len(actions),
            "new_follow_count": len(followed_ids),
            "like_count": len(liked_ids),
            "paired_count": len(paired_ids),
            "duplicate_action_keys": duplicate_action_keys,
        },
        "ledger": {
            "event_count": len(ledger_events),
            "candidate_count": len(ledger_progress),
            "new_follow_evidence_count": len(new_follow_evidence_ids),
            "paired_like_evidence_count": len(paired_like_evidence_ids),
            "malformed_line_count": malformed_lines,
        },
        "summary": {
            "status": summary.get("status"),
            "completed": summary.get("completed"),
            "failures": summary.get("failures"),
            "published": summary.get("published"),
        },
        "heartbeat": {
            "status": heartbeat.get("status"),
            "error_present": bool(heartbeat.get("error_code")),
        },
        "log": {
            "day_line_count": len(day_lines),
            "completion_present": log_completion_present,
            "publish_confirmation_present": log_publish_present,
            "error_count": log_error_count,
        },
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify one paired ROOM engagement run from a read-only database copy."
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--heartbeat", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--routine-date", required=True)
    parser.add_argument("--goal", type=int, default=EXPECTED_GOAL)
    parser.add_argument("--policy-id", default=EXPECTED_POLICY_ID)
    parser.add_argument("--require-clean-log", action="store_true")
    args = parser.parse_args()

    if args.database.resolve() == LIVE_DATABASE_PATH.resolve():
        parser.error("Refusing to open the live operations.db; pass a consistent copy.")
    if args.goal != EXPECTED_GOAL:
        parser.error(f"paired production verification requires --goal {EXPECTED_GOAL}")

    try:
        report = verify_engagement(
            database_path=args.database,
            ledger_path=args.ledger,
            summary_path=args.summary,
            heartbeat_path=args.heartbeat,
            log_path=args.log,
            routine_date=args.routine_date,
            goal=args.goal,
            policy_id=args.policy_id,
            require_clean_log=args.require_clean_log,
        )
    except (OSError, ValueError, sqlite3.Error) as exc:
        report = {
            "routine_date": args.routine_date,
            "policy_id": args.policy_id,
            "goal": args.goal,
            "ok": False,
            "clean_run": False,
            "errors": [f"verifier_error_{type(exc).__name__}"],
            "warnings": [],
        }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
