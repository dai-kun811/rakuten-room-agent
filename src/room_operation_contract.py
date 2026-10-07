from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _load_jst():
    fallback = timezone(timedelta(hours=9), name="Asia/Tokyo")
    try:
        candidate = ZoneInfo("Asia/Tokyo")
    except ZoneInfoNotFoundError:
        return fallback
    # Also fail closed if a test double or broken timezone database returns a
    # zone with the wrong offset. Japan is UTC+09:00 throughout the year.
    probe = datetime(2026, 1, 1, tzinfo=timezone.utc).astimezone(candidate)
    return candidate if probe.utcoffset() == timedelta(hours=9) else fallback


# Windows Python installations do not always bundle the IANA database. Japan
# has no daylight-saving transition, so the fallback remains exact and never
# depends on the Windows system locale.
JST = _load_jst()
POST_SLOTS = ("morning", "noon", "evening")
MANIFEST_SCHEMA_VERSION = 2
INCIDENT_SCHEMA_VERSION = 1


class ContractError(ValueError):
    """Raised when operational data violates the migration contract."""


class TransitionError(ContractError):
    """Raised when a state transition is unsafe or undefined."""


class SlotStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    CLAIMED = "claimed"
    SUBMITTING = "submitting"
    SUBMITTED_UNCONFIRMED = "submitted_unconfirmed"
    POSTED = "posted"
    FAILED_PRE_SUBMIT = "failed_pre_submit"
    UNCERTAIN = "uncertain"
    BLOCKED = "blocked"
    EXPIRED_UNPOSTED = "expired_unposted"


class IncidentStatus(str, Enum):
    OPEN = "open"
    AUTO_RECOVERING = "auto_recovering"
    CODEX_QUEUED = "codex_queued"
    CODEX_WORKING = "codex_working"
    VALIDATING = "validating"
    RESOLVED = "resolved"
    NEEDS_HUMAN = "needs_human"
    BUDGET_EXHAUSTED = "budget_exhausted"


class IncidentRoute(str, Enum):
    AUTO_RETRY = "auto_retry"
    CODEX_RECOMMENDED = "codex_recommended"
    USER_ACTION_REQUIRED = "user_action_required"
    OPERATIONS_REQUIRED = "operations_required"


class ReasonCode(str, Enum):
    TRANSIENT_NETWORK = "transient_network"
    RATE_LIMITED = "rate_limited"
    ACTIONS_RUN_MISSING = "actions_run_missing"
    ACTIONS_RUN_DELAYED = "actions_run_delayed"
    ARTIFACT_DELAYED = "artifact_delayed"
    PROFILE_LOCK_BUSY = "profile_lock_busy"
    FAILED_PRE_SUBMIT = "failed_pre_submit"
    QUALITY_POOL_EXHAUSTED = "quality_pool_exhausted"
    CLASSIFICATION_UNSUPPORTED = "classification_unsupported"
    COPY_VALIDATION_REGRESSION = "copy_validation_regression"
    MANIFEST_SCHEMA_MISMATCH = "manifest_schema_mismatch"
    API_SCHEMA_CHANGED = "api_schema_changed"
    AUTH_EXPIRED = "auth_expired"
    CAPTCHA = "captcha"
    TWO_FACTOR_REQUIRED = "two_factor_required"
    ACCOUNT_RESTRICTED = "account_restricted"
    POST_RESULT_UNCERTAIN = "post_result_uncertain"
    DUPLICATE_RISK = "duplicate_risk"
    ROOM_LEDGER_MISMATCH = "room_ledger_mismatch"
    DATABASE_CORRUPT = "database_corrupt"
    DISK_FULL = "disk_full"
    WINDOWS_TASK_DISABLED = "windows_task_disabled"
    HEARTBEAT_MISSING = "heartbeat_missing"
    GIT_DIVERGED = "git_diverged"
    SECRET_OR_AUTH_CHANGE_REQUIRED = "secret_or_auth_change_required"


SLOT_TRANSITIONS: dict[SlotStatus, frozenset[SlotStatus]] = {
    SlotStatus.PENDING: frozenset(
        {SlotStatus.READY, SlotStatus.BLOCKED, SlotStatus.EXPIRED_UNPOSTED}
    ),
    SlotStatus.READY: frozenset(
        {SlotStatus.CLAIMED, SlotStatus.BLOCKED, SlotStatus.EXPIRED_UNPOSTED}
    ),
    SlotStatus.CLAIMED: frozenset(
        {
            SlotStatus.SUBMITTING,
            SlotStatus.FAILED_PRE_SUBMIT,
            SlotStatus.EXPIRED_UNPOSTED,
        }
    ),
    SlotStatus.SUBMITTING: frozenset(
        {SlotStatus.SUBMITTED_UNCONFIRMED, SlotStatus.UNCERTAIN}
    ),
    SlotStatus.SUBMITTED_UNCONFIRMED: frozenset(
        {SlotStatus.POSTED, SlotStatus.UNCERTAIN}
    ),
    SlotStatus.FAILED_PRE_SUBMIT: frozenset(
        {SlotStatus.CLAIMED, SlotStatus.BLOCKED, SlotStatus.EXPIRED_UNPOSTED}
    ),
    SlotStatus.UNCERTAIN: frozenset(
        {SlotStatus.POSTED, SlotStatus.FAILED_PRE_SUBMIT}
    ),
    SlotStatus.BLOCKED: frozenset(
        {SlotStatus.READY, SlotStatus.EXPIRED_UNPOSTED}
    ),
    SlotStatus.POSTED: frozenset(),
    SlotStatus.EXPIRED_UNPOSTED: frozenset(),
}


INCIDENT_TRANSITIONS: dict[IncidentStatus, frozenset[IncidentStatus]] = {
    IncidentStatus.OPEN: frozenset(
        {
            IncidentStatus.AUTO_RECOVERING,
            IncidentStatus.CODEX_QUEUED,
            IncidentStatus.NEEDS_HUMAN,
            IncidentStatus.BUDGET_EXHAUSTED,
        }
    ),
    IncidentStatus.AUTO_RECOVERING: frozenset(
        {
            IncidentStatus.RESOLVED,
            IncidentStatus.CODEX_QUEUED,
            IncidentStatus.NEEDS_HUMAN,
            IncidentStatus.BUDGET_EXHAUSTED,
        }
    ),
    IncidentStatus.CODEX_QUEUED: frozenset(
        {
            IncidentStatus.CODEX_WORKING,
            IncidentStatus.NEEDS_HUMAN,
            IncidentStatus.BUDGET_EXHAUSTED,
        }
    ),
    IncidentStatus.CODEX_WORKING: frozenset(
        {
            IncidentStatus.VALIDATING,
            IncidentStatus.NEEDS_HUMAN,
            IncidentStatus.BUDGET_EXHAUSTED,
        }
    ),
    IncidentStatus.VALIDATING: frozenset(
        {
            IncidentStatus.RESOLVED,
            IncidentStatus.CODEX_WORKING,
            IncidentStatus.NEEDS_HUMAN,
            IncidentStatus.BUDGET_EXHAUSTED,
        }
    ),
    IncidentStatus.RESOLVED: frozenset(),
    IncidentStatus.NEEDS_HUMAN: frozenset(),
    IncidentStatus.BUDGET_EXHAUSTED: frozenset(),
}


REASON_ROUTES: dict[ReasonCode, IncidentRoute] = {
    ReasonCode.TRANSIENT_NETWORK: IncidentRoute.AUTO_RETRY,
    ReasonCode.RATE_LIMITED: IncidentRoute.AUTO_RETRY,
    ReasonCode.ACTIONS_RUN_MISSING: IncidentRoute.AUTO_RETRY,
    ReasonCode.ACTIONS_RUN_DELAYED: IncidentRoute.AUTO_RETRY,
    ReasonCode.ARTIFACT_DELAYED: IncidentRoute.AUTO_RETRY,
    ReasonCode.PROFILE_LOCK_BUSY: IncidentRoute.AUTO_RETRY,
    ReasonCode.FAILED_PRE_SUBMIT: IncidentRoute.AUTO_RETRY,
    ReasonCode.QUALITY_POOL_EXHAUSTED: IncidentRoute.CODEX_RECOMMENDED,
    ReasonCode.CLASSIFICATION_UNSUPPORTED: IncidentRoute.CODEX_RECOMMENDED,
    ReasonCode.COPY_VALIDATION_REGRESSION: IncidentRoute.CODEX_RECOMMENDED,
    ReasonCode.MANIFEST_SCHEMA_MISMATCH: IncidentRoute.CODEX_RECOMMENDED,
    ReasonCode.API_SCHEMA_CHANGED: IncidentRoute.CODEX_RECOMMENDED,
    ReasonCode.AUTH_EXPIRED: IncidentRoute.USER_ACTION_REQUIRED,
    ReasonCode.CAPTCHA: IncidentRoute.USER_ACTION_REQUIRED,
    ReasonCode.TWO_FACTOR_REQUIRED: IncidentRoute.USER_ACTION_REQUIRED,
    ReasonCode.ACCOUNT_RESTRICTED: IncidentRoute.USER_ACTION_REQUIRED,
    ReasonCode.POST_RESULT_UNCERTAIN: IncidentRoute.USER_ACTION_REQUIRED,
    ReasonCode.DUPLICATE_RISK: IncidentRoute.USER_ACTION_REQUIRED,
    ReasonCode.ROOM_LEDGER_MISMATCH: IncidentRoute.USER_ACTION_REQUIRED,
    ReasonCode.GIT_DIVERGED: IncidentRoute.USER_ACTION_REQUIRED,
    ReasonCode.SECRET_OR_AUTH_CHANGE_REQUIRED: IncidentRoute.USER_ACTION_REQUIRED,
    ReasonCode.DATABASE_CORRUPT: IncidentRoute.OPERATIONS_REQUIRED,
    ReasonCode.DISK_FULL: IncidentRoute.OPERATIONS_REQUIRED,
    ReasonCode.WINDOWS_TASK_DISABLED: IncidentRoute.OPERATIONS_REQUIRED,
    ReasonCode.HEARTBEAT_MISSING: IncidentRoute.OPERATIONS_REQUIRED,
}


@dataclass(frozen=True)
class RetryBudget:
    transient_attempts: int = 2
    actions_dispatches: int = 1
    artifact_fetch_attempts: int = 2
    pre_submit_retries: int = 1
    database_restores: int = 1
    codex_fix_cycles: int = 2
    actions_recovery_runs: int = 2
    codex_minutes_per_incident: int = 45
    codex_minutes_per_day: int = 60
    codex_same_fingerprint_per_day: int = 1


DEFAULT_RETRY_BUDGET = RetryBudget()


def routine_date_jst(now: datetime) -> date:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ContractError("routine date requires a timezone-aware datetime")
    return now.astimezone(JST).date()


def posting_window_open(
    routine_date: date | str,
    now: datetime,
    *,
    cutoff: time = time(22, 30),
) -> bool:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ContractError("posting window requires a timezone-aware datetime")
    day = routine_date if isinstance(routine_date, date) else _parse_iso_date(str(routine_date))
    local = now.astimezone(JST)
    return local.date() == day and local.time().replace(tzinfo=None) < cutoff


def validate_slot_transition(
    current: SlotStatus | str,
    target: SlotStatus | str,
    *,
    manual_resolution: bool = False,
) -> None:
    source = SlotStatus(current)
    destination = SlotStatus(target)
    if destination not in SLOT_TRANSITIONS[source]:
        raise TransitionError(f"unsafe slot transition: {source.value} -> {destination.value}")
    if source is SlotStatus.UNCERTAIN and destination is SlotStatus.FAILED_PRE_SUBMIT:
        if not manual_resolution:
            raise TransitionError("uncertain can be cleared only by explicit human resolution")


def validate_incident_transition(
    current: IncidentStatus | str,
    target: IncidentStatus | str,
) -> None:
    source = IncidentStatus(current)
    destination = IncidentStatus(target)
    if destination not in INCIDENT_TRANSITIONS[source]:
        raise TransitionError(
            f"unsafe incident transition: {source.value} -> {destination.value}"
        )


def route_for_reason(reason: ReasonCode | str) -> IncidentRoute:
    return REASON_ROUTES[ReasonCode(reason)]


def normalize_product_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme != "https" or not parsed.netloc:
        raise ContractError("product_url must be an absolute https URL")
    normalized_path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), normalized_path, "", ""))


def build_idempotency_key(
    routine_date: date | str,
    slot: str,
    product_url: str,
    content_hash: str,
) -> str:
    day = routine_date.isoformat() if isinstance(routine_date, date) else str(routine_date)
    _parse_iso_date(day)
    if slot not in POST_SLOTS:
        raise ContractError(f"unknown post slot: {slot}")
    normalized_url = normalize_product_url(product_url)
    _validate_sha256(content_hash, "content_hash")
    raw = "\n".join((day, slot, normalized_url, content_hash.lower()))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def incident_fingerprint(
    reason: ReasonCode | str,
    routine_date: date | str,
    *,
    slot: str = "",
    component: str = "",
) -> str:
    code = ReasonCode(reason).value
    day = routine_date.isoformat() if isinstance(routine_date, date) else str(routine_date)
    _parse_iso_date(day)
    if slot and slot not in POST_SLOTS:
        raise ContractError(f"unknown post slot: {slot}")
    # The fingerprint identifies the root cause and scope, not the calendar
    # day.  The state store scopes deduplication by routine_date separately so
    # an unresolved incident from yesterday cannot suppress today's incident.
    canonical = json.dumps(
        {"component": component.strip(), "reason": code, "slot": slot},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_manifest_v2(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ContractError("manifest schema_version must be 2")
    _reject_secret_fields(payload)
    _parse_iso_date(_required_text(payload, "routine_date_jst"))
    revision = payload.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ContractError("manifest revision must be a positive integer")
    supersedes = payload.get("supersedes_revision")
    if supersedes is not None and (
        not isinstance(supersedes, int)
        or isinstance(supersedes, bool)
        or supersedes < 1
        or supersedes >= revision
    ):
        raise ContractError("supersedes_revision must be lower than revision")
    _required_text(payload, "actions_run_id")
    head_sha = _required_text(payload, "head_sha")
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", head_sha):
        raise ContractError("head_sha must be a 7-40 character hexadecimal git SHA")
    generated_at = _required_text(payload, "generated_at")
    _parse_aware_datetime(generated_at, "generated_at")
    slots = payload.get("slots")
    if not isinstance(slots, Mapping) or set(slots) != set(POST_SLOTS):
        raise ContractError("manifest slots must contain exactly morning, noon, and evening")
    for slot in POST_SLOTS:
        _validate_manifest_slot(slot, slots[slot])


def _validate_manifest_slot(slot: str, value: Any) -> None:
    if not isinstance(value, Mapping):
        raise ContractError(f"manifest slot {slot} must be an object")
    status = value.get("status")
    if status not in {"ready", "blocked"}:
        raise ContractError(f"manifest slot {slot} has invalid status")
    if status == "blocked":
        _required_text(value, "reason")
        if value.get("candidate") not in (None, ""):
            raise ContractError(f"blocked manifest slot {slot} must not contain a candidate")
        return
    candidate = value.get("candidate")
    if not isinstance(candidate, Mapping):
        raise ContractError(f"ready manifest slot {slot} requires a candidate")
    product_url = _required_text(candidate, "product_url")
    normalized_url = _required_text(candidate, "normalized_url")
    if normalize_product_url(product_url) != normalize_product_url(normalized_url):
        raise ContractError(f"manifest slot {slot} normalized_url does not match product_url")
    _required_text(candidate, "product_name")
    _required_text(candidate, "product_type")
    _required_text(candidate, "body")
    content_hash = _required_text(candidate, "content_hash")
    _validate_sha256(content_hash, f"manifest slot {slot} content_hash")
    quality = candidate.get("quality")
    if not isinstance(quality, Mapping) or quality.get("status") != "passed":
        raise ContractError(f"manifest slot {slot} candidate quality must be passed")
    errors = quality.get("errors")
    if errors not in ([], ()):
        raise ContractError(f"manifest slot {slot} candidate quality errors must be empty")


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, (str, int)) or isinstance(value, bool) or not str(value).strip():
        raise ContractError(f"{key} is required")
    return str(value).strip()


def _parse_iso_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(f"invalid ISO date: {value}") from exc
    if parsed.isoformat() != value:
        raise ContractError(f"date must use YYYY-MM-DD: {value}")
    return parsed


def _parse_aware_datetime(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{field} must be an ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"{field} must include a timezone offset")
    return parsed


def _validate_sha256(value: str, field: str) -> None:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise ContractError(f"{field} must be a SHA-256 hex digest")


def _reject_secret_fields(value: Any) -> None:
    secret_names = {
        "api_key",
        "authorization",
        "cookie",
        "password",
        "secret",
        "token",
        "google_service_account_json",
        "rakuten_application_id",
        "rakuten_access_key",
    }
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in secret_names:
                raise ContractError(f"secret-like field is forbidden: {key}")
            _reject_secret_fields(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_secret_fields(child)
