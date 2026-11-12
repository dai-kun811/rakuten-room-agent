"""Schedule-aware ROOM/X coordinator.

The worker prepares one immutable daily document, waits for exact ROOM
publication evidence on promotion days, and delegates the final API call to
``room_x_publisher``.  It never falls back to a different item or late post.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from room_operation_contract import JST
from room_x_api import DpapiTokenStore, XApiClient, XApiError
from room_x_content_planner import (
    HELPFUL_PATTERN_BY_WEEKDAY,
    PROMO_WEEKDAYS,
    XContentPlanError,
    _require_plan_integrity,
    plan_x_content,
    release_promotion,
)
from room_x_publisher import XPublishError, publish_document


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = PROJECT_ROOT / ".local" / "room-worker"
X_STATE_ROOT = STATE_ROOT / "x-publisher"
DATABASE_PATH = STATE_ROOT / "operations.db"
HELPFUL_CARDS_PATH = PROJECT_ROOT / "config" / "room-x-helpful-cards.json"
TOKEN_STORE_PATH = X_STATE_ROOT / "oauth-token.dpapi"
PROMO_SLOT_TIMES = {
    "morning": time(8, 5),
    "noon": time(12, 5),
    "evening": time(19, 5),
}
ROOM_SLOT_DUE_TIMES = {
    "morning": time(8, 0),
    "noon": time(12, 0),
    "evening": time(19, 0),
}
HELPFUL_TIMES = {
    0: time(9, 10),   # Monday morning
    2: time(20, 10),  # Wednesday evening
    4: time(9, 10),   # Friday morning
    6: time(20, 10),  # Sunday evening
}
WINDOW_MINUTES = 15


class XScheduleError(RuntimeError):
    """A scheduling, input, or evidence gate failure."""


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _product_id(url: str) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.scheme != "https" or parsed.netloc.lower() != "item.rakuten.co.jp" or len(parts) < 2:
        raise XScheduleError("manifest product URL is not a canonical Rakuten item URL")
    return "/".join(parts[:2]).lower()


def _canonical_product_url(url: str) -> str:
    return f"https://item.rakuten.co.jp/{_product_id(url)}"


def build_promo_copy(candidate: Mapping[str, Any]) -> str:
    label = str(candidate.get("short_product_label", "")).strip()
    checkpoints = [
        value for value in str(candidate.get("purchase_checkpoints", "")).split() if value
    ][:3]
    if not label or len(checkpoints) < 2:
        raise XScheduleError("promotion candidate lacks a product label or purchase checkpoints")
    checks = "・".join(checkpoints)
    return (
        f"{label}を選ぶ前に確認したいポイント。\n"
        f"{checks}を見比べると、家庭に合うか判断しやすくなります。\n"
        "#PR"
    )


def snapshot_from_manifest(
    manifest: Mapping[str, Any],
    *,
    recent_promoted_product_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    day = str(manifest.get("routine_date_jst", ""))
    try:
        date.fromisoformat(day)
    except ValueError as exc:
        raise XScheduleError("manifest routine date is invalid") from exc
    slots = manifest.get("slots")
    if not isinstance(slots, Mapping):
        raise XScheduleError("manifest slots are missing")
    candidates = []
    for slot in ("morning", "noon", "evening"):
        value = slots.get(slot)
        if not isinstance(value, Mapping):
            raise XScheduleError(f"manifest slot is missing: {slot}")
        candidate = value.get("candidate")
        if not isinstance(candidate, Mapping):
            continue
        quality = candidate.get("quality")
        quality = quality if isinstance(quality, Mapping) else {}
        errors = quality.get("errors", [])
        if not isinstance(errors, list):
            errors = ["invalid_quality_errors"]
        product_url = _canonical_product_url(
            str(candidate.get("normalized_url") or candidate.get("product_url") or "")
        )
        candidates.append(
            {
                "slot": slot,
                "product_id": _product_id(product_url),
                "product_url": product_url,
                "title": str(candidate.get("title", "")),
                "category": str(candidate.get("product_type", "")),
                "quality_score": int(quality.get("score", 0)),
                "ready": value.get("status") == "ready" and quality.get("status") == "passed",
                "review_reasons": [str(item) for item in errors if str(item).strip()],
                "promo_copy": build_promo_copy(candidate),
            }
        )
    return {
        "version": 1,
        "plan_date": day,
        "recent_promoted_product_ids": list(recent_promoted_product_ids),
        "room_candidates": candidates,
    }


def helpful_snapshot(day: date, cards_payload: Mapping[str, Any]) -> dict[str, Any]:
    cards = cards_payload.get("cards")
    if cards_payload.get("version") != 1 or not isinstance(cards, list):
        raise XScheduleError("helpful card catalog is invalid")
    return {
        "version": 1,
        "plan_date": day.isoformat(),
        "recent_promoted_product_ids": [],
        "recent_helpful_content_hashes": [],
        "helpful_cards": cards,
    }


def recent_plan_history(state_root: Path, *, before: date) -> tuple[tuple[str, ...], tuple[str, ...]]:
    promoted: list[str] = []
    helpful_hashes: list[str] = []
    plans_dir = Path(state_root) / "plans"
    if not plans_dir.is_dir():
        return (), ()
    for path in sorted(plans_dir.glob("*.json"), reverse=True)[:30]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            plan_day = date.fromisoformat(str(value.get("plan_date", "")))
        except (OSError, json.JSONDecodeError, ValueError):
            raise XScheduleError("stored X plan history is invalid")
        if plan_day >= before:
            continue
        binding = value.get("binding")
        if value.get("content_kind") == "promotion" and isinstance(binding, Mapping):
            promoted.append(str(binding.get("product_id", "")))
        if value.get("content_kind") == "helpful":
            helpful_hashes.append(str(value.get("helpful_content_hash", "")))
    return (
        tuple(item for item in promoted if item),
        tuple(item for item in helpful_hashes if item),
    )


def store_immutable(path: Path, value: Mapping[str, Any], *, id_field: str) -> dict[str, Any]:
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get(id_field) != value.get(id_field):
            raise XScheduleError(f"immutable {path.name} conflicts with the current input")
        return existing
    _atomic_json(path, value)
    return dict(value)


def acquire_release_claim(path: Path, plan: Mapping[str, Any]) -> dict[str, Any]:
    claim = {
        "version": 1,
        "claim_id": hashlib.sha256(
            f"{plan['idempotency_key']}:{plan['plan_id']}".encode("utf-8")
        ).hexdigest()[:24],
        "state": "claimed",
        "idempotency_key": plan["idempotency_key"],
        "plan_id": plan["plan_id"],
        "claimed_at": datetime.now(tz=JST).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("plan_id") != plan.get("plan_id"):
            raise XScheduleError("release claim conflicts with the immutable plan")
        return existing
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(claim, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    return claim


def posted_database_evidence(
    database_path: Path,
    *,
    day: str,
    slot: str,
    product_url: str,
) -> dict[str, str]:
    uri = f"file:{Path(database_path).resolve().as_posix()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as connection:
            connection.row_factory = sqlite3.Row
            with closing(
                connection.execute(
                    """
                    SELECT s.status, s.normalized_url, a.attempt_id, a.status AS attempt_status
                    FROM slots s
                    JOIN post_attempts a
                      ON a.routine_date = s.routine_date AND a.slot = s.slot
                    WHERE s.routine_date = ? AND s.slot = ?
                    ORDER BY a.created_at DESC, a.rowid DESC LIMIT 1
                    """,
                    (day, slot),
                )
            ) as cursor:
                row = cursor.fetchone()
    except sqlite3.Error as exc:
        raise XScheduleError("ROOM operations database could not be read") from exc
    if row is None or row["status"] != "posted" or row["attempt_status"] != "posted":
        raise XScheduleError("matching ROOM slot is not posted")
    if str(row["normalized_url"]) != product_url:
        raise XScheduleError("posted ROOM slot does not match the prepared product")
    return {
        "status": "posted",
        "slot": slot,
        "product_url": product_url,
        "posted_evidence_id": str(row["attempt_id"]),
    }


def in_publish_window(now: datetime, plan: Mapping[str, Any]) -> bool:
    local = now.astimezone(JST)
    if str(plan.get("plan_date")) != local.date().isoformat():
        return False
    if plan.get("content_kind") == "promotion":
        binding = plan.get("binding")
        if not isinstance(binding, Mapping) or binding.get("slot") not in PROMO_SLOT_TIMES:
            return False
        target = PROMO_SLOT_TIMES[str(binding["slot"])]
    elif plan.get("content_kind") == "helpful":
        target = HELPFUL_TIMES.get(local.weekday())
        if target is None:
            return False
    else:
        return False
    start = datetime.combine(local.date(), target, tzinfo=JST)
    return start <= local < start + timedelta(minutes=WINDOW_MINUTES)


def prepare_daily_plan(
    *,
    now: datetime,
    manifest: Mapping[str, Any] | None,
    cards_payload: Mapping[str, Any] | None,
    state_root: Path = X_STATE_ROOT,
) -> dict[str, Any]:
    day = now.astimezone(JST).date()
    plan_path = Path(state_root) / "plans" / f"{day.isoformat()}.json"
    if plan_path.is_file():
        try:
            existing = json.loads(plan_path.read_text(encoding="utf-8"))
            _require_plan_integrity(existing)
        except (OSError, json.JSONDecodeError, XContentPlanError) as exc:
            raise XScheduleError("stored daily X plan failed integrity validation") from exc
        return existing
    promoted, helpful_hashes = recent_plan_history(Path(state_root), before=day)
    if day.weekday() in PROMO_WEEKDAYS:
        if manifest is None:
            raise XScheduleError("promotion day requires today's ROOM manifest")
        snapshot = snapshot_from_manifest(
            manifest, recent_promoted_product_ids=promoted
        )
    else:
        if cards_payload is None:
            raise XScheduleError("helpful day requires the reviewed card catalog")
        snapshot = helpful_snapshot(day, cards_payload)
        snapshot["recent_helpful_content_hashes"] = list(helpful_hashes)
    try:
        plan = plan_x_content(snapshot)
    except XContentPlanError as exc:
        raise XScheduleError(str(exc)) from exc
    if plan.get("content_kind") == "promotion":
        binding = plan.get("binding")
        slot = str(binding.get("slot", "")) if isinstance(binding, Mapping) else ""
        due = ROOM_SLOT_DUE_TIMES.get(slot)
        if due is None or now.astimezone(JST).time().replace(tzinfo=None) >= due:
            raise XScheduleError("promotion plan was not prepared before its ROOM slot")
    return store_immutable(plan_path, plan, id_field="plan_id")


def release_promotion_from_evidence(
    plan: Mapping[str, Any],
    *,
    database_path: Path,
    room_detail_lookup: Callable[[str], Mapping[str, str]],
    state_root: Path = X_STATE_ROOT,
) -> dict[str, Any]:
    binding = plan.get("binding")
    if not isinstance(binding, Mapping):
        raise XScheduleError("promotion binding is missing")
    day = str(plan["plan_date"])
    release_path = Path(state_root) / "releases" / f"{day}.json"
    if release_path.is_file():
        return json.loads(release_path.read_text(encoding="utf-8"))
    evidence = posted_database_evidence(
        database_path,
        day=day,
        slot=str(binding["slot"]),
        product_url=str(binding["product_url"]),
    )
    room_evidence = room_detail_lookup(str(binding["product_url"]))
    evidence.update(
        {
            "product_id": str(binding["product_id"]),
            "room_detail_url": str(room_evidence.get("room_detail_url", "")),
            "room_detail_product_url": str(room_evidence.get("product_url", "")),
        }
    )
    claim = acquire_release_claim(
        Path(state_root) / "release-claims" / f"{day}.json", plan
    )
    try:
        release = release_promotion(plan, evidence, durable_claim=claim)
    except XContentPlanError as exc:
        raise XScheduleError(str(exc)) from exc
    return store_immutable(release_path, release, id_field="release_id")


def publish_scheduled_document(
    document: Mapping[str, Any],
    *,
    now: datetime,
    state_root: Path,
    media_path: Path | None,
    token_store_path: Path,
    client_id: str,
    client_secret: str,
) -> dict[str, Any]:
    if not in_publish_window(now, document):
        raise XScheduleError("current time is outside the scheduled X publication window")
    store = DpapiTokenStore(token_store_path)
    bundle = store.load()
    client = XApiClient()
    refreshed = client.refresh_token(
        bundle, client_id=client_id, client_secret=client_secret, now=now.astimezone(timezone.utc)
    )
    if refreshed != bundle:
        store.save(refreshed)
    return publish_document(
        document,
        state_dir=state_root,
        client=client,
        access_token=refreshed.access_token,
        media_path=media_path,
        today=now.astimezone(JST).date(),
    )


def _helpful_media_path(plan: Mapping[str, Any], cards_payload: Mapping[str, Any]) -> Path:
    card = plan.get("card")
    if not isinstance(card, Mapping):
        raise XScheduleError("helpful plan card is missing")
    card_id = str(card.get("card_id", ""))
    for item in cards_payload.get("cards", []):
        if isinstance(item, Mapping) and str(item.get("card_id", "")) == card_id:
            value = Path(str(item.get("visual_path", "")))
            path = value if value.is_absolute() else PROJECT_ROOT / value
            if not path.is_file():
                raise XScheduleError("reviewed helpful visual is missing")
            return path
    raise XScheduleError("helpful plan card is missing from the reviewed catalog")


def run_once(*, now: datetime, apply: bool) -> dict[str, Any]:
    from local_room_worker import github_headers, github_token
    from room_orchestrator_worker import fetch_latest_manifest
    from room_x_room_evidence import find_room_detail_for_product

    local = now.astimezone(JST)
    cards_payload = None
    manifest = None
    existing_plan_path = X_STATE_ROOT / "plans" / f"{local.date().isoformat()}.json"
    if local.weekday() not in PROMO_WEEKDAYS:
        try:
            cards_payload = json.loads(HELPFUL_CARDS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise XScheduleError("reviewed helpful card catalog cannot be read") from exc
    elif not existing_plan_path.is_file():
        import requests

        with requests.Session() as session:
            _, manifest = fetch_latest_manifest(
                session,
                headers=github_headers(github_token()),
                now=local,
            )
    plan = prepare_daily_plan(
        now=local,
        manifest=manifest,
        cards_payload=cards_payload,
    )
    if not in_publish_window(local, plan):
        return {"status": "planned", "plan_id": plan["plan_id"]}
    document: Mapping[str, Any] = plan
    media_path = None
    if plan.get("content_kind") == "promotion":
        document = release_promotion_from_evidence(
            plan,
            database_path=DATABASE_PATH,
            room_detail_lookup=lambda product_url: find_room_detail_for_product(
                product_url,
                profile_dir=STATE_ROOT / "chrome-profile",
                profile_lock_path=STATE_ROOT / "chrome-profile.lock",
                headless=True,
            ),
        )
    else:
        assert cards_payload is not None
        media_path = _helpful_media_path(plan, cards_payload)
    if not apply:
        return {
            "status": "ready_not_published",
            "document_id": document.get("release_id") or document.get("plan_id"),
        }
    return publish_scheduled_document(
        document,
        now=local,
        state_root=X_STATE_ROOT,
        media_path=media_path,
        token_store_path=TOKEN_STORE_PATH,
        client_id=os.environ.get("X_OAUTH_CLIENT_ID", ""),
        client_secret=os.environ.get("X_OAUTH_CLIENT_SECRET", ""),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare and publish the scheduled reviewed X content")
    parser.add_argument("--apply", action="store_true", help="allow the official X API create call")
    args = parser.parse_args()
    try:
        result = run_once(now=datetime.now(tz=JST), apply=args.apply)
    except (XScheduleError, XContentPlanError, XApiError, XPublishError) as exc:
        print(json.dumps({"status": "stopped", "reason": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
