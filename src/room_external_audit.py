"""Read-only external audit for Phase 9.

The report intentionally contains only operational statuses and counts.  It
never emits product URLs, post bodies, candidate IDs, or credentials.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from room_engagement_state import audit as audit_engagement
from room_operation_contract import JST
from room_state_store import RoomStateStore
from room_phase8_runner import audit_day


STATE_ROOT = Path(__file__).resolve().parents[1] / ".local" / "room-worker"
DATABASE_PATH = STATE_ROOT / "operations.db"
LEDGER_PATH = STATE_ROOT / "post-ledger.jsonl"
REPORT_PATH = STATE_ROOT / "external-audit.json"
ORCHESTRATOR_HEARTBEAT = STATE_ROOT / "orchestrator-heartbeat.json"
ENGAGEMENT_HEARTBEAT = STATE_ROOT / "engagement-heartbeat.json"


def _heartbeat_status(path: Path, *, now: datetime, max_age_minutes: int) -> tuple[str, str]:
    if not path.exists():
        return "missing", "heartbeat_missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        stamp = datetime.fromisoformat(str(payload["updated_at"])).astimezone(JST)
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return "invalid", "heartbeat_invalid"
    role = str(payload.get("role", ""))
    heartbeat_status = str(payload.get("status", ""))
    # The engagement worker is intentionally a once-daily job.  A terminal
    # heartbeat from the same JST routine date is current evidence even when
    # it is older than the rolling age used for the repeatedly scheduled
    # posting orchestrator.
    if (
        role == "engagement"
        and str(payload.get("routine_date", "")) == now.astimezone(JST).date().isoformat()
        and heartbeat_status in {"completed", "skipped", "partial", "needs_human"}
    ):
        return "ok", ""
    age = (now.astimezone(JST) - stamp).total_seconds()
    if age > max_age_minutes * 60:
        return "stale", "heartbeat_stale"
    if heartbeat_status in {"failed", "generation_failed", "audit_failed"}:
        return "failed", "heartbeat_failed"
    if heartbeat_status == "partial":
        return "partial", "heartbeat_partial"
    return "ok", ""


def audit(*, now: datetime | None = None, routine_date: str | None = None) -> dict:
    current = (now or datetime.now(tz=JST)).astimezone(JST)
    day = routine_date or current.date().isoformat()
    store = RoomStateStore(DATABASE_PATH)
    store.initialize()
    errors: list[str] = []
    try:
        store.quick_check()
    except Exception:
        errors.append("sqlite_corrupt")
    snapshot = store.export_snapshot(day)
    slot_statuses = {slot: str(value.get("status", "missing")) for slot, value in snapshot.get("slots", {}).items()}
    active_uncertain = sum(1 for status in slot_statuses.values() if status == "uncertain")
    if active_uncertain:
        errors.append("uncertain_slot")
    if any(status == "blocked" for status in slot_statuses.values()):
        errors.append("partial_manifest")
    open_incidents = [item for item in snapshot.get("incidents", []) if item.get("status") not in {"resolved", "budget_exhausted"}]
    if open_incidents:
        errors.append("重大incident_open")
    posting_audit = audit_day(store, LEDGER_PATH, day, sync_ledger=False)
    if current.time().replace(tzinfo=None).hour >= 20 and not posting_audit["ok"]:
        errors.append("posting_audit_failed")
    engagement = audit_engagement(DATABASE_PATH, routine_date=day, now=current)
    if not engagement["ok"]:
        errors.extend(f"engagement:{item}" for item in engagement["errors"])
    orchestrator_heartbeat, orchestrator_error = _heartbeat_status(ORCHESTRATOR_HEARTBEAT, now=current, max_age_minutes=180)
    engagement_heartbeat, engagement_error = _heartbeat_status(ENGAGEMENT_HEARTBEAT, now=current, max_age_minutes=180)
    if orchestrator_error and current.time().replace(tzinfo=None).hour >= 8:
        errors.append(orchestrator_error)
    if engagement_error and current.time().replace(tzinfo=None).hour >= 8:
        errors.append(engagement_error)
    report = {
        "routine_date": day,
        "checked_at": current.isoformat(),
        "ok": not errors,
        "errors": sorted(set(errors)),
        "slot_statuses": slot_statuses,
        "posting_audit_ok": bool(posting_audit["ok"]),
        "engagement": {key: value for key, value in engagement.items() if key not in {"errors"}},
        "heartbeats": {"orchestrator": orchestrator_heartbeat, "engagement": engagement_heartbeat},
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a sanitized read-only ROOM external audit.")
    parser.add_argument("--routine-date", default="")
    args = parser.parse_args()
    result = audit(routine_date=args.routine_date or None)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
