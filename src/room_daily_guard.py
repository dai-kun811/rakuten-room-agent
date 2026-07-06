from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from local_room_worker import (
    LEDGER_PATH,
    PROFILE_DIR,
    REPO_API,
    actions_run_is_today,
    fetch_latest_generation_report,
    github_headers,
    github_token,
    parse_post_windows,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_WORKER = PROJECT_ROOT / "src" / "local_room_worker.py"
AUTH_PROBE = PROJECT_ROOT / "src" / "room_auth_probe.py"
STATE_DIR = PROJECT_ROOT / ".local" / "room-worker"
LOG_PATH = STATE_DIR / "daily-guard.log"
SUMMARY_PATH = STATE_DIR / "daily-guard-summary.json"
REQUIRED_SLOTS = ("morning", "noon", "evening")
SAFE_RETRY_DETAILS = {"TimeoutError"}


class DailyGuardError(RuntimeError):
    pass


def configure_logging() -> logging.Logger:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
    )
    return logging.getLogger("room-daily-guard")


def report_has_all_slots(report: dict[str, Any]) -> bool:
    required = tuple(str(slot) for slot in report.get("required_post_slots", []))
    missing = [str(slot) for slot in report.get("missing_post_slots", [])]
    ready_slots = {
        str(item.get("post_slot", ""))
        for item in report.get("items", [])
        if item.get("status") == "ready"
    }
    return required == REQUIRED_SLOTS and not missing and set(REQUIRED_SLOTS) <= ready_slots


def today_runs(runs: list[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    return [run for run in runs if actions_run_is_today(run, now)]


def fetch_workflow_runs(session: Any, headers: dict[str, str]) -> list[dict[str, Any]]:
    response = session.get(
        f"{REPO_API}/actions/workflows/daily.yml/runs",
        headers=headers,
        params={"per_page": 20},
        timeout=30,
    )
    response.raise_for_status()
    return list(response.json().get("workflow_runs", []))


def ensure_generation_ready(
    session: Any,
    *,
    headers: dict[str, str],
    now: datetime | None = None,
    timeout_seconds: int = 15 * 60,
    poll_seconds: int = 15,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.monotonic()
    dispatched = False
    while True:
        runs = today_runs(fetch_workflow_runs(session, headers), now)
        active = next(
            (run for run in runs if run.get("status") in {"queued", "in_progress", "pending"}),
            None,
        )
        if active is None:
            successful = next(
                (run for run in runs if run.get("conclusion") == "success"),
                None,
            )
            if successful is not None:
                run, report = fetch_latest_generation_report(session, headers=headers)
                if run.get("id") == successful.get("id") and report_has_all_slots(report):
                    return run, report
                raise DailyGuardError("Today's successful generation report is missing required slots.")

            failed = next(
                (run for run in runs if run.get("status") == "completed"),
                None,
            )
            if failed is not None:
                raise DailyGuardError(
                    f"Today's generation run failed; automatic repeat blocked run={failed.get('id')}."
                )

            if not dispatched:
                response = session.post(
                    f"{REPO_API}/actions/workflows/daily.yml/dispatches",
                    headers=headers,
                    json={"ref": "main"},
                    timeout=30,
                )
                response.raise_for_status()
                dispatched = True

        if time.monotonic() - started >= timeout_seconds:
            raise DailyGuardError("Timed out waiting for today's generation run.")
        time.sleep(poll_seconds)


def due_slot_labels(
    now: datetime | None = None,
    *,
    windows: list[tuple[str, int, int]] | None = None,
) -> list[str]:
    local_now = now or datetime.now().astimezone()
    configured = windows or parse_post_windows(os.getenv("ROOM_POST_WINDOWS"))
    return [label for label, start, _end in configured if local_now.hour >= start]


def read_latest_slot_events(path: Path = LEDGER_PATH) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    latest: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        slot = str(event.get("post_slot", "")).strip()
        if slot:
            latest[slot] = event
    return latest


def report_item_for_slot(report: dict[str, Any], label: str) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in report.get("items", [])
            if item.get("status") == "ready"
            and item.get("post_slot") == label
            and item.get("product_url")
        ),
        None,
    )


def run_no_post_probe(product_url: str) -> bool:
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(AUTH_PROBE),
                product_url,
                "--profile-dir",
                str(PROFILE_DIR),
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False
    return completed.returncode == 0


def run_post_worker(label: str, *, retry_detail: str = "") -> int:
    env = os.environ.copy()
    env["ROOM_FORCE_POST_SLOT"] = label
    if retry_detail:
        env["ROOM_RETRY_FAILED_DETAILS"] = retry_detail
    else:
        env.pop("ROOM_RETRY_FAILED_DETAILS", None)
    try:
        completed = subprocess.run(
            [sys.executable, str(LOCAL_WORKER)],
            cwd=PROJECT_ROOT,
            env=env,
            timeout=5 * 60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 1
    return completed.returncode


def ensure_slot_posted(
    report: dict[str, Any],
    label: str,
    *,
    now: datetime | None = None,
    logger: logging.Logger,
) -> None:
    local_now = now or datetime.now().astimezone()
    slot = f"{local_now.date().isoformat()}:{label}"
    item = report_item_for_slot(report, label)
    if item is None:
        raise DailyGuardError(f"Ready item is missing for slot={label}.")

    latest = read_latest_slot_events().get(slot, {})
    if latest.get("status") == "posted":
        logger.info("Post already confirmed slot=%s", slot)
        return
    if latest.get("status") == "reserved":
        raise DailyGuardError(f"Post outcome is uncertain; automatic retry blocked slot={slot}.")

    retry_detail = ""
    if latest.get("status") == "failed":
        detail = str(latest.get("detail", ""))
        if detail not in SAFE_RETRY_DETAILS:
            raise DailyGuardError(
                f"Post failed with non-retryable detail slot={slot} detail={detail}."
            )
        if not run_no_post_probe(str(item["product_url"])):
            raise DailyGuardError(f"No-post auth probe failed slot={slot}.")
        retry_detail = detail

    run_post_worker(label, retry_detail=retry_detail)
    latest = read_latest_slot_events().get(slot, {})
    if latest.get("status") == "posted":
        logger.info("Post confirmed slot=%s", slot)
        return

    if latest.get("status") == "failed" and not retry_detail:
        detail = str(latest.get("detail", ""))
        if detail in SAFE_RETRY_DETAILS and run_no_post_probe(str(item["product_url"])):
            run_post_worker(label, retry_detail=detail)
            latest = read_latest_slot_events().get(slot, {})
            if latest.get("status") == "posted":
                logger.info("Post confirmed after bounded retry slot=%s", slot)
                return
    raise DailyGuardError(
        f"Post was not confirmed slot={slot} status={latest.get('status', 'missing')}."
    )


def write_summary(payload: dict[str, Any]) -> None:
    SUMMARY_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def run(mode: str) -> int:
    logger = configure_logging()
    now = datetime.now().astimezone()
    summary: dict[str, Any] = {
        "checked_at": now.isoformat(),
        "routine_date": now.date().isoformat(),
        "mode": mode,
        "status": "failed",
        "confirmed_slots": [],
    }
    try:
        import requests

        with requests.Session() as session:
            run_data, report = ensure_generation_ready(
                session,
                headers=github_headers(github_token()),
                now=now,
            )
        summary["actions_run_id"] = run_data.get("id")
        summary["report_run_id"] = report.get("run_id")
        if mode == "generation":
            summary["status"] = "ready"
            logger.info("Generation guard confirmed all slots run=%s", run_data.get("id"))
        else:
            for label in due_slot_labels(now):
                ensure_slot_posted(report, label, now=now, logger=logger)
                summary["confirmed_slots"].append(label)
            summary["status"] = "posted"
            logger.info("Post guard confirmed slots=%s", summary["confirmed_slots"])
        write_summary(summary)
        return 0
    except Exception as exc:
        summary["error"] = str(exc)[:500]
        write_summary(summary)
        logger.error("Daily guard failed mode=%s error=%s", mode, str(exc))
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Guard daily Rakuten ROOM generation and posts.")
    parser.add_argument("mode", choices=("generation", "post"))
    args = parser.parse_args()
    return run(args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
