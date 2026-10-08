from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping
from uuid import uuid4

from room_operation_contract import (
    INCIDENT_SCHEMA_VERSION,
    POST_SLOTS,
    IncidentRoute,
    IncidentStatus,
    ReasonCode,
    SlotStatus,
    TransitionError,
    incident_fingerprint,
    build_idempotency_key,
    normalize_product_url,
    route_for_reason,
    RetryBudget,
    DEFAULT_RETRY_BUDGET,
    validate_manifest_v2,
    validate_incident_transition,
    validate_slot_transition,
)


DATABASE_SCHEMA_VERSION = 1
DEFINITIVE_LEGACY_PRE_SUBMIT_FAILURES = {
    "楽天商品ページにROOM投稿ボタンが見つかりません。",
}


class StateStoreError(RuntimeError):
    pass


class StateConflictError(StateStoreError):
    pass


class StateIntegrityError(StateStoreError):
    pass


class RetryBudgetExhausted(StateStoreError):
    """Raised when an incident has consumed its bounded retry budget."""


@dataclass(frozen=True)
class RetryReservation:
    incident_id: str
    budget_key: str
    attempt: int
    limit: int
    exhausted: bool


@dataclass(frozen=True)
class LegacySyncResult:
    written_lines: int
    duplicate_lines: int
    skipped_slots: int


@dataclass(frozen=True)
class CatchUpPlan:
    expired_slots: tuple[str, ...]
    human_review_slots: tuple[str, ...]
    missing_today_slots: tuple[str, ...]


@dataclass(frozen=True)
class PostAttemptRecord:
    attempt_id: str
    idempotency_key: str
    routine_date: str
    slot: str
    status: str
    submit_started: bool


@dataclass(frozen=True)
class SlotRecord:
    routine_date: str
    slot: str
    status: SlotStatus
    manifest_revision: int | None
    normalized_url: str
    content_hash: str
    product_type: str
    version: int
    updated_at: str


@dataclass(frozen=True)
class LegacyImportResult:
    imported_lines: int
    duplicate_lines: int
    malformed_lines: int
    affected_slots: int


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS days (
    routine_date TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'in_progress',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS manifests (
    routine_date TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    manifest_hash TEXT NOT NULL,
    actions_run_id TEXT NOT NULL,
    head_sha TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    accepted_at TEXT NOT NULL,
    PRIMARY KEY (routine_date, revision),
    UNIQUE (manifest_hash)
);

CREATE TABLE IF NOT EXISTS slots (
    routine_date TEXT NOT NULL,
    slot TEXT NOT NULL CHECK (slot IN ('morning', 'noon', 'evening')),
    status TEXT NOT NULL,
    manifest_revision INTEGER,
    normalized_url TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    product_type TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (routine_date, slot),
    FOREIGN KEY (routine_date) REFERENCES days(routine_date)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_posted_product_url
ON slots(normalized_url)
WHERE status = 'posted' AND normalized_url <> '';

CREATE TABLE IF NOT EXISTS post_attempts (
    attempt_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    routine_date TEXT NOT NULL,
    slot TEXT NOT NULL,
    normalized_url TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    submit_started INTEGER NOT NULL DEFAULT 0 CHECK (submit_started IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (routine_date, slot) REFERENCES slots(routine_date, slot)
);

CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    routine_date TEXT NOT NULL,
    slot TEXT NOT NULL DEFAULT '',
    component TEXT NOT NULL DEFAULT '',
    reason_code TEXT NOT NULL,
    route TEXT NOT NULL,
    status TEXT NOT NULL,
    last_safe_state TEXT NOT NULL,
    manifest_revision INTEGER,
    attempts_json TEXT NOT NULL DEFAULT '{}',
    lease_owner TEXT,
    lease_expires_at TEXT,
    next_action TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (fingerprint, status)
);

CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    routine_date TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_key TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS legacy_imports (
    line_hash TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    source_line INTEGER NOT NULL,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recovery_controls (
    recovery_id TEXT PRIMARY KEY,
    routine_date TEXT NOT NULL,
    old_run_id TEXT NOT NULL,
    expected_head_sha TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    status TEXT NOT NULL,
    owner_id TEXT,
    lease_expires_at TEXT,
    budget_consumed INTEGER NOT NULL DEFAULT 0 CHECK (budget_consumed IN (0, 1)),
    replacement_run_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class RoomStateStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        # Complements SQLite CAS inside one process. Cross-process execution is
        # serialized by the orchestrator lock file before any browser action.
        self.execution_lock = threading.RLock()

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
        except Exception:
            connection.close()
            raise
        return connection

    def initialize(self) -> None:
        with closing(self.connect()) as connection:
            connection.executescript(SCHEMA_SQL)
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO metadata(key, value) VALUES ('schema_version', ?)",
                    (str(DATABASE_SCHEMA_VERSION),),
                )
            elif int(row["value"]) != DATABASE_SCHEMA_VERSION:
                raise StateIntegrityError(
                    f"unsupported database schema version: {row['value']}"
                )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def quick_check(self) -> None:
        with closing(self.connect()) as connection:
            rows = connection.execute("PRAGMA quick_check").fetchall()
        results = [str(row[0]) for row in rows]
        if results != ["ok"]:
            raise StateIntegrityError("database quick_check failed: " + "; ".join(results))

    def reserve_recovery_control(
        self,
        *,
        recovery_id: str,
        routine_date: date | str,
        old_run_id: str,
        expected_head_sha: str,
        revision: int,
        owner_id: str,
        lease_seconds: int = 3600,
        now: datetime | None = None,
    ) -> None:
        if not recovery_id or not old_run_id or not expected_head_sha or not owner_id:
            raise StateStoreError("recovery control identity is required")
        if revision < 1 or lease_seconds < 1:
            raise StateStoreError("recovery revision and lease must be positive")
        self.initialize()
        base = now or datetime.now().astimezone()
        timestamp = _timestamp(base)
        expires = _timestamp(base + timedelta(seconds=lease_seconds))
        day = _date_text(routine_date)
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT 1 FROM recovery_controls WHERE recovery_id = ?", (recovery_id,)
            ).fetchone()
            if existing is not None:
                raise StateConflictError("recovery_id has already consumed its budget")
            connection.execute(
                """INSERT INTO recovery_controls(
                   recovery_id, routine_date, old_run_id, expected_head_sha, revision,
                   status, owner_id, lease_expires_at, budget_consumed, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, 1, ?, ?)""",
                (recovery_id, day, old_run_id, expected_head_sha, revision, owner_id, expires, timestamp, timestamp),
            )

    def complete_recovery_control(
        self,
        recovery_id: str,
        *,
        owner_id: str,
        replacement_run_id: str | None = None,
        now: datetime | None = None,
    ) -> None:
        timestamp = _timestamp(now)
        with self.transaction() as connection:
            result = connection.execute(
                """UPDATE recovery_controls
                   SET status = 'ready', owner_id = NULL, lease_expires_at = NULL,
                       replacement_run_id = COALESCE(?, replacement_run_id), updated_at = ?
                   WHERE recovery_id = ? AND status = 'running' AND owner_id = ?""",
                (replacement_run_id, timestamp, recovery_id, owner_id),
            )
            if result.rowcount != 1:
                raise StateConflictError("recovery lease is missing or expired")

    def rebind_preflight_recovery_control(
        self,
        recovery_id: str,
        *,
        owner_id: str,
        expected_head_sha: str,
        now: datetime | None = None,
    ) -> None:
        """Rebind only a failed-before-generation lease to the actual dispatch ref."""
        timestamp = _timestamp(now)
        with self.transaction() as connection:
            result = connection.execute(
                """UPDATE recovery_controls
                   SET expected_head_sha = ?, updated_at = ?
                   WHERE recovery_id = ? AND status = 'running' AND owner_id = ?
                     AND replacement_run_id IS NULL""",
                (expected_head_sha, timestamp, recovery_id, owner_id),
            )
            if result.rowcount != 1:
                raise StateConflictError("recovery control cannot be rebound")

    def assert_recovery_control(
        self,
        recovery_id: str,
        *,
        expected_head_sha: str,
        revision: int,
    ) -> dict[str, Any]:
        self.initialize()
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM recovery_controls WHERE recovery_id = ?", (recovery_id,)
            ).fetchone()
        if row is None:
            raise StateConflictError("recovery control is not registered")
        if str(row["expected_head_sha"]) != expected_head_sha or int(row["revision"]) != revision:
            raise StateConflictError("recovery control fence does not match")
        if str(row["status"]) not in {"running", "ready", "posting_allowed"}:
            raise StateConflictError("recovery control is not usable")
        return dict(row)

    def backup_to(self, destination: Path | str) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        source_connection = self.connect()
        target_connection = sqlite3.connect(target)
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
            source_connection.close()
        return target

    def ensure_day(self, routine_date: date | str, *, now: datetime | None = None) -> None:
        day = _date_text(routine_date)
        timestamp = _timestamp(now)
        with self.transaction() as connection:
            self._ensure_day(connection, day, timestamp)

    def get_slot(self, routine_date: date | str, slot: str) -> SlotRecord | None:
        day = _date_text(routine_date)
        _validate_slot(slot)
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM slots WHERE routine_date = ? AND slot = ?",
                (day, slot),
            ).fetchone()
        return _slot_record(row) if row is not None else None

    def create_slot(
        self,
        routine_date: date | str,
        slot: str,
        *,
        status: SlotStatus = SlotStatus.PENDING,
        manifest_revision: int | None = None,
        normalized_url: str = "",
        content_hash: str = "",
        product_type: str = "",
        now: datetime | None = None,
        source: str = "state_store",
    ) -> SlotRecord:
        day = _date_text(routine_date)
        _validate_slot(slot)
        timestamp = _timestamp(now)
        normalized = normalize_product_url(normalized_url) if normalized_url else ""
        with self.transaction() as connection:
            self._ensure_day(connection, day, timestamp)
            try:
                connection.execute(
                    """
                    INSERT INTO slots(
                        routine_date, slot, status, manifest_revision,
                        normalized_url, content_hash, product_type, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        day,
                        slot,
                        status.value,
                        manifest_revision,
                        normalized,
                        content_hash,
                        product_type,
                        timestamp,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise StateConflictError(f"slot already exists or conflicts: {day}:{slot}") from exc
            self._append_event(
                connection,
                routine_date=day,
                aggregate_type="slot",
                aggregate_key=f"{day}:{slot}",
                from_status=None,
                to_status=status.value,
                payload={"source": source},
                timestamp=timestamp,
            )
            row = connection.execute(
                "SELECT * FROM slots WHERE routine_date = ? AND slot = ?",
                (day, slot),
            ).fetchone()
        assert row is not None
        return _slot_record(row)

    def transition_slot(
        self,
        routine_date: date | str,
        slot: str,
        *,
        expected_status: SlotStatus,
        expected_version: int,
        target_status: SlotStatus,
        manual_resolution: bool = False,
        payload: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> SlotRecord:
        day = _date_text(routine_date)
        _validate_slot(slot)
        validate_slot_transition(
            expected_status,
            target_status,
            manual_resolution=manual_resolution,
        )
        timestamp = _timestamp(now)
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM slots WHERE routine_date = ? AND slot = ?",
                (day, slot),
            ).fetchone()
            if row is None:
                raise StateConflictError(f"slot does not exist: {day}:{slot}")
            if row["status"] != expected_status.value or row["version"] != expected_version:
                raise StateConflictError(
                    f"stale slot state: expected={expected_status.value}@{expected_version} "
                    f"actual={row['status']}@{row['version']}"
                )
            updated = connection.execute(
                """
                UPDATE slots
                SET status = ?, version = version + 1, updated_at = ?
                WHERE routine_date = ? AND slot = ? AND status = ? AND version = ?
                """,
                (
                    target_status.value,
                    timestamp,
                    day,
                    slot,
                    expected_status.value,
                    expected_version,
                ),
            )
            if updated.rowcount != 1:
                raise StateConflictError(f"slot compare-and-swap failed: {day}:{slot}")
            self._append_event(
                connection,
                routine_date=day,
                aggregate_type="slot",
                aggregate_key=f"{day}:{slot}",
                from_status=expected_status.value,
                to_status=target_status.value,
                payload=dict(payload or {}),
                timestamp=timestamp,
            )
            new_row = connection.execute(
                "SELECT * FROM slots WHERE routine_date = ? AND slot = ?",
                (day, slot),
            ).fetchone()
        assert new_row is not None
        return _slot_record(new_row)

    def claim_post_attempt(
        self,
        routine_date: date | str,
        slot: str,
        *,
        expected_version: int,
        now: datetime | None = None,
    ) -> PostAttemptRecord:
        day = _date_text(routine_date)
        _validate_slot(slot)
        timestamp = _timestamp(now)
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM slots WHERE routine_date = ? AND slot = ?", (day, slot)
            ).fetchone()
            if row is None or row["status"] != SlotStatus.READY.value or row["version"] != expected_version:
                actual = "missing" if row is None else f"{row['status']}@{row['version']}"
                raise StateConflictError(f"slot is not claimable: {day}:{slot} actual={actual}")
            if not row["normalized_url"] or not row["content_hash"]:
                raise StateIntegrityError("claim requires manifest URL and content hash")
            base_key = build_idempotency_key(day, slot, row["normalized_url"], row["content_hash"])
            confirmed_absent = connection.execute(
                """SELECT COUNT(*) AS count FROM post_attempts
                   WHERE routine_date = ? AND slot = ?
                     AND normalized_url = ? AND content_hash = ?
                     AND status = 'confirmed_not_posted'""",
                (day, slot, row["normalized_url"], row["content_hash"]),
            ).fetchone()["count"]
            if int(confirmed_absent) > DEFAULT_RETRY_BUDGET.pre_submit_retries:
                raise StateConflictError("confirmed-absence retry budget exhausted")
            key = base_key if int(confirmed_absent) == 0 else f"{base_key}:retry{confirmed_absent}"
            attempt_id = f"{day}-{slot}-{uuid4().hex[:12]}"
            try:
                connection.execute(
                    """INSERT INTO post_attempts(
                           attempt_id, idempotency_key, routine_date, slot,
                           normalized_url, content_hash, status, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (attempt_id, key, day, slot, row["normalized_url"], row["content_hash"], SlotStatus.CLAIMED.value, timestamp, timestamp),
                )
            except sqlite3.IntegrityError as exc:
                raise StateConflictError("duplicate post attempt rejected by idempotency key") from exc
            claimed = connection.execute(
                "UPDATE slots SET status = ?, version = version + 1, updated_at = ? WHERE routine_date = ? AND slot = ? AND status = ? AND version = ?",
                (SlotStatus.CLAIMED.value, timestamp, day, slot, SlotStatus.READY.value, expected_version),
            )
            if claimed.rowcount != 1:
                raise StateConflictError("slot claim compare-and-swap failed")
            self._append_event(
                connection, routine_date=day, aggregate_type="slot", aggregate_key=f"{day}:{slot}",
                from_status=SlotStatus.READY.value, to_status=SlotStatus.CLAIMED.value,
                payload={"attempt_id": attempt_id, "idempotency_key": key}, timestamp=timestamp,
            )
        return PostAttemptRecord(attempt_id, key, day, slot, SlotStatus.CLAIMED.value, False)

    def reset_failed_pre_submit(self, routine_date: date | str, slot: str, *, now: datetime | None = None) -> SlotRecord:
        """Return a definitely-not-submitted attempt to READY for its single retry."""
        day = _date_text(routine_date)
        timestamp = _timestamp(now)
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM slots WHERE routine_date = ? AND slot = ?", (day, slot)).fetchone()
            if row is None or row["status"] != SlotStatus.FAILED_PRE_SUBMIT.value:
                raise StateConflictError("slot is not a failed pre-submit attempt")
            attempt = connection.execute("SELECT * FROM post_attempts WHERE routine_date = ? AND slot = ? ORDER BY created_at DESC, rowid DESC LIMIT 1", (day, slot)).fetchone()
            if attempt is None or attempt["status"] != SlotStatus.FAILED_PRE_SUBMIT.value or int(attempt["submit_started"]):
                raise StateConflictError("failed attempt is not safely retryable")
            # Preserve the failed attempt for audit while releasing its unique
            # idempotency key for the one bounded retry.  submit_started=0 is
            # checked above, so this cannot hide a possibly-sent submission.
            reclaimed_key = f"{attempt['idempotency_key']}:reclaimed:{attempt['attempt_id']}"
            connection.execute(
                """UPDATE post_attempts
                   SET idempotency_key = ?, status = 'failed_pre_submit_reclaimed', updated_at = ?
                   WHERE attempt_id = ? AND status = ? AND submit_started = 0""",
                (reclaimed_key, timestamp, attempt["attempt_id"], SlotStatus.FAILED_PRE_SUBMIT.value),
            )
            connection.execute("UPDATE slots SET status = ?, version = version + 1, updated_at = ? WHERE routine_date = ? AND slot = ? AND status = ?", (SlotStatus.READY.value, timestamp, day, slot, SlotStatus.FAILED_PRE_SUBMIT.value))
            self._append_event(connection, routine_date=day, aggregate_type="slot", aggregate_key=f"{day}:{slot}", from_status=SlotStatus.FAILED_PRE_SUBMIT.value, to_status=SlotStatus.READY.value, payload={"event": "bounded_pre_submit_retry", "preserved_attempt_id": str(attempt["attempt_id"])}, timestamp=timestamp)
            updated = connection.execute("SELECT * FROM slots WHERE routine_date = ? AND slot = ?", (day, slot)).fetchone()
        assert updated is not None
        return _slot_record(updated)

    def advance_post_attempt(
        self,
        attempt_id: str,
        *,
        expected_slot_status: SlotStatus,
        target_slot_status: SlotStatus,
        expected_attempt_status: str,
        target_attempt_status: str,
        submit_started: bool | None = None,
        now: datetime | None = None,
    ) -> SlotRecord:
        validate_slot_transition(expected_slot_status, target_slot_status)
        timestamp = _timestamp(now)
        with self.transaction() as connection:
            attempt = connection.execute(
                "SELECT * FROM post_attempts WHERE attempt_id = ?", (attempt_id,)
            ).fetchone()
            if attempt is None or attempt["status"] != expected_attempt_status:
                actual = "missing" if attempt is None else str(attempt["status"])
                raise StateConflictError(f"stale post attempt: expected={expected_attempt_status} actual={actual}")
            day, slot = str(attempt["routine_date"]), str(attempt["slot"])
            row = connection.execute(
                "SELECT * FROM slots WHERE routine_date = ? AND slot = ?", (day, slot)
            ).fetchone()
            if row is None or row["status"] != expected_slot_status.value:
                actual = "missing" if row is None else str(row["status"])
                raise StateConflictError(f"stale slot during post attempt: expected={expected_slot_status.value} actual={actual}")
            next_submit_started = int(attempt["submit_started"] if submit_started is None else submit_started)
            attempt_updated = connection.execute(
                "UPDATE post_attempts SET status = ?, submit_started = ?, updated_at = ? WHERE attempt_id = ? AND status = ?",
                (target_attempt_status, next_submit_started, timestamp, attempt_id, expected_attempt_status),
            )
            slot_updated = connection.execute(
                "UPDATE slots SET status = ?, version = version + 1, updated_at = ? WHERE routine_date = ? AND slot = ? AND status = ?",
                (target_slot_status.value, timestamp, day, slot, expected_slot_status.value),
            )
            if attempt_updated.rowcount != 1 or slot_updated.rowcount != 1:
                raise StateConflictError("post attempt compare-and-swap failed")
            self._append_event(
                connection, routine_date=day, aggregate_type="slot", aggregate_key=f"{day}:{slot}",
                from_status=expected_slot_status.value, to_status=target_slot_status.value,
                payload={"attempt_id": attempt_id}, timestamp=timestamp,
            )
            updated = connection.execute(
                "SELECT * FROM slots WHERE routine_date = ? AND slot = ?", (day, slot)
            ).fetchone()
        assert updated is not None
        return _slot_record(updated)

    def get_post_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM post_attempts WHERE attempt_id = ?", (attempt_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def get_active_post_attempt(
        self, routine_date: date | str, slot: str
    ) -> dict[str, Any] | None:
        day = _date_text(routine_date)
        _validate_slot(slot)
        with closing(self.connect()) as connection:
            row = connection.execute(
                """SELECT * FROM post_attempts
                   WHERE routine_date = ? AND slot = ?
                     AND status NOT IN (
                         'posted','failed_pre_submit','failed_pre_submit_reclaimed',
                         'confirmed_not_posted'
                     )
                   ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                (day, slot),
            ).fetchone()
        return dict(row) if row is not None else None

    def create_incident(
        self,
        routine_date: date | str,
        *,
        reason: ReasonCode,
        last_safe_state: str,
        next_action: str,
        slot: str = "",
        component: str = "",
        manifest_revision: int | None = None,
        now: datetime | None = None,
    ) -> str:
        day = _date_text(routine_date)
        if slot:
            _validate_slot(slot)
        timestamp = _timestamp(now)
        fingerprint = incident_fingerprint(
            reason,
            day,
            slot=slot,
            component=component,
        )
        with self.transaction() as connection:
            existing = connection.execute(
                """
                SELECT incident_id FROM incidents
                WHERE fingerprint = ? AND routine_date = ?
                  AND status NOT IN ('resolved', 'needs_human', 'budget_exhausted')
                ORDER BY created_at DESC LIMIT 1
                """,
                (fingerprint, day),
            ).fetchone()
            if existing is not None:
                return str(existing["incident_id"])
            incident_id = f"{day}-{reason.value}-{uuid4().hex[:8]}"
            connection.execute(
                """
                INSERT INTO incidents(
                    incident_id, schema_version, fingerprint, routine_date, slot,
                    component, reason_code, route, status, last_safe_state,
                    manifest_revision, next_action, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    incident_id,
                    INCIDENT_SCHEMA_VERSION,
                    fingerprint,
                    day,
                    slot,
                    component,
                    reason.value,
                    route_for_reason(reason).value,
                    IncidentStatus.OPEN.value,
                    last_safe_state,
                    manifest_revision,
                    next_action,
                    timestamp,
                    timestamp,
                ),
            )
            self._append_event(
                connection,
                routine_date=day,
                aggregate_type="incident",
                aggregate_key=incident_id,
                from_status=None,
                to_status=IncidentStatus.OPEN.value,
                payload={"reason_code": reason.value, "route": route_for_reason(reason).value},
                timestamp=timestamp,
            )
        return incident_id

    def transition_incident(
        self,
        incident_id: str,
        *,
        expected_status: IncidentStatus,
        target_status: IncidentStatus,
        next_action: str,
        lease_owner: str | None = None,
        manual_resolution: bool = False,
        now: datetime | None = None,
    ) -> None:
        validate_incident_transition(
            expected_status, target_status, manual_resolution=manual_resolution
        )
        timestamp = _timestamp(now)
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM incidents WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
            if row is None or row["status"] != expected_status.value:
                actual = "missing" if row is None else row["status"]
                raise StateConflictError(
                    f"stale incident state: expected={expected_status.value} actual={actual}"
                )
            if lease_owner is not None:
                lease = connection.execute(
                    "SELECT lease_owner, lease_expires_at FROM incidents WHERE incident_id = ?",
                    (incident_id,),
                ).fetchone()
                if not _lease_is_owned(lease, lease_owner, timestamp):
                    raise StateConflictError("incident lease is missing or expired")
            terminal_fingerprint = (
                self._terminal_incident_fingerprint(connection, row, target_status)
                if target_status in {
                    IncidentStatus.RESOLVED,
                    IncidentStatus.NEEDS_HUMAN,
                    IncidentStatus.BUDGET_EXHAUSTED,
                }
                else str(row["fingerprint"])
            )
            updated = connection.execute(
                """
                UPDATE incidents SET fingerprint = ?, status = ?, next_action = ?, updated_at = ?
                WHERE incident_id = ? AND status = ?
                """,
                (
                    terminal_fingerprint,
                    target_status.value,
                    next_action,
                    timestamp,
                    incident_id,
                    expected_status.value,
                ),
            )
            if updated.rowcount != 1:
                raise StateConflictError("incident compare-and-swap failed")
            self._append_event(
                connection,
                routine_date=row["routine_date"],
                aggregate_type="incident",
                aggregate_key=incident_id,
                from_status=expected_status.value,
                to_status=target_status.value,
                payload={
                    "next_action": next_action,
                    "root_cause_fingerprint": self._root_cause_incident_fingerprint(row),
                },
                timestamp=timestamp,
            )

    def resolve_uncertain_as_posted(
        self,
        routine_date: date | str,
        slot: str,
        *,
        evidence_source: str,
        evidence_note: str,
        now: datetime | None = None,
    ) -> SlotRecord:
        """Reconcile a human-confirmed ROOM post without sending again."""
        day = _date_text(routine_date)
        _validate_slot(slot)
        source = evidence_source.strip()
        note = evidence_note.strip()
        if not source or not note:
            raise StateStoreError("human evidence source and note are required")
        timestamp = _timestamp(now)
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM slots WHERE routine_date = ? AND slot = ?",
                (day, slot),
            ).fetchone()
            attempt = connection.execute(
                """SELECT * FROM post_attempts WHERE routine_date = ? AND slot = ?
                   ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                (day, slot),
            ).fetchone()
            if row is None or row["status"] != SlotStatus.UNCERTAIN.value:
                actual = "missing" if row is None else str(row["status"])
                raise StateConflictError(
                    f"slot is not human-resolvable uncertain: {day}:{slot} actual={actual}"
                )
            if (
                attempt is None
                or attempt["status"] != SlotStatus.UNCERTAIN.value
                or not int(attempt["submit_started"])
            ):
                raise StateConflictError("uncertain attempt is not safely submitted")
            incident = connection.execute(
                """SELECT * FROM incidents WHERE routine_date = ? AND slot = ?
                   AND reason_code = ? AND status = ? ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                (day, slot, ReasonCode.POST_RESULT_UNCERTAIN.value, IncidentStatus.NEEDS_HUMAN.value),
            ).fetchone()
            if incident is None:
                raise StateConflictError("needs-human post-result incident is missing")
            updated_attempt = connection.execute(
                """UPDATE post_attempts SET status = ?, updated_at = ?
                   WHERE attempt_id = ? AND status = ? AND submit_started = 1""",
                (SlotStatus.POSTED.value, timestamp, attempt["attempt_id"], SlotStatus.UNCERTAIN.value),
            )
            updated_slot = connection.execute(
                """UPDATE slots SET status = ?, version = version + 1, updated_at = ?
                   WHERE routine_date = ? AND slot = ? AND status = ? AND version = ?""",
                (SlotStatus.POSTED.value, timestamp, day, slot, SlotStatus.UNCERTAIN.value, row["version"]),
            )
            if updated_attempt.rowcount != 1 or updated_slot.rowcount != 1:
                raise StateConflictError("human post resolution compare-and-swap failed")
            terminal_fingerprint = self._terminal_incident_fingerprint(connection, incident)
            resolved_incident = connection.execute(
                """UPDATE incidents SET fingerprint = ?, status = ?, next_action = ?, updated_at = ?
                   WHERE incident_id = ? AND status = ?""",
                (terminal_fingerprint, IncidentStatus.RESOLVED.value,
                 "authenticated ROOM confirmed post; no repost", timestamp,
                 incident["incident_id"], IncidentStatus.NEEDS_HUMAN.value),
            )
            if resolved_incident.rowcount != 1:
                raise StateConflictError("authenticated post incident resolution failed")
            self._append_event(
                connection, routine_date=day, aggregate_type="slot", aggregate_key=f"{day}:{slot}",
                from_status=SlotStatus.UNCERTAIN.value, to_status=SlotStatus.POSTED.value,
                payload={"attempt_id": attempt["attempt_id"], "event": "human_confirmed_room_post",
                         "evidence_source": source, "evidence_note": note, "reposted": False},
                timestamp=timestamp,
            )
            self._append_event(
                connection, routine_date=day, aggregate_type="incident", aggregate_key=str(incident["incident_id"]),
                from_status=IncidentStatus.NEEDS_HUMAN.value, to_status=IncidentStatus.RESOLVED.value,
                payload={"event": "authenticated_room_confirmed_post", "evidence_source": source,
                         "root_cause_fingerprint": self._root_cause_incident_fingerprint(incident),
                         "reposted": False},
                timestamp=timestamp,
            )
            updated = connection.execute(
                "SELECT * FROM slots WHERE routine_date = ? AND slot = ?", (day, slot)
            ).fetchone()
        assert updated is not None
        return _slot_record(updated)

    def resolve_uncertain_as_not_posted(
        self,
        routine_date: date | str,
        slot: str,
        *,
        evidence_source: str,
        evidence_note: str,
        now: datetime | None = None,
    ) -> SlotRecord:
        """Authorize one retry after authenticated ROOM proves the send absent.

        The original submit-started attempt is retained as immutable audit
        evidence.  A later claim receives a suffixed idempotency key, and the
        configured one-retry budget prevents an unbounded resend loop.
        """
        day = _date_text(routine_date)
        _validate_slot(slot)
        source = evidence_source.strip()
        note = evidence_note.strip()
        if not source or not note:
            raise StateStoreError("authenticated absence evidence source and note are required")
        timestamp = _timestamp(now)
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM slots WHERE routine_date = ? AND slot = ?",
                (day, slot),
            ).fetchone()
            attempt = connection.execute(
                """SELECT * FROM post_attempts WHERE routine_date = ? AND slot = ?
                   ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                (day, slot),
            ).fetchone()
            if row is None or row["status"] != SlotStatus.UNCERTAIN.value:
                actual = "missing" if row is None else str(row["status"])
                raise StateConflictError(
                    f"slot is not evidence-resolvable uncertain: {day}:{slot} actual={actual}"
                )
            if (
                attempt is None
                or attempt["status"] != SlotStatus.UNCERTAIN.value
                or not int(attempt["submit_started"])
            ):
                raise StateConflictError("uncertain attempt is not a submitted attempt")
            incident = connection.execute(
                """SELECT * FROM incidents WHERE routine_date = ? AND slot = ?
                   AND reason_code = ? AND status = ? ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                (day, slot, ReasonCode.POST_RESULT_UNCERTAIN.value, IncidentStatus.NEEDS_HUMAN.value),
            ).fetchone()
            if incident is None:
                raise StateConflictError("needs-human post-result incident is missing")
            previous_absent = connection.execute(
                """SELECT COUNT(*) AS count FROM post_attempts
                   WHERE routine_date = ? AND slot = ?
                     AND normalized_url = ? AND content_hash = ?
                     AND status = 'confirmed_not_posted'""",
                (day, slot, row["normalized_url"], row["content_hash"]),
            ).fetchone()["count"]
            if int(previous_absent) >= DEFAULT_RETRY_BUDGET.pre_submit_retries:
                raise RetryBudgetExhausted("confirmed-absence retry budget exhausted")
            attempts = json.loads(str(incident["attempts_json"] or "{}"))
            attempts["pre_submit_retries"] = int(previous_absent) + 1
            updated_attempt = connection.execute(
                """UPDATE post_attempts SET status = 'confirmed_not_posted', updated_at = ?
                   WHERE attempt_id = ? AND status = ? AND submit_started = 1""",
                (timestamp, attempt["attempt_id"], SlotStatus.UNCERTAIN.value),
            )
            failed = connection.execute(
                """UPDATE slots SET status = ?, version = version + 1, updated_at = ?
                   WHERE routine_date = ? AND slot = ? AND status = ? AND version = ?""",
                (SlotStatus.FAILED_PRE_SUBMIT.value, timestamp, day, slot,
                 SlotStatus.UNCERTAIN.value, row["version"]),
            )
            ready = connection.execute(
                """UPDATE slots SET status = ?, version = version + 1, updated_at = ?
                   WHERE routine_date = ? AND slot = ? AND status = ?""",
                (SlotStatus.READY.value, timestamp, day, slot, SlotStatus.FAILED_PRE_SUBMIT.value),
            )
            if updated_attempt.rowcount != 1 or failed.rowcount != 1 or ready.rowcount != 1:
                raise StateConflictError("authenticated absence resolution compare-and-swap failed")
            terminal_fingerprint = self._terminal_incident_fingerprint(connection, incident)
            resolved_incident = connection.execute(
                """UPDATE incidents SET fingerprint = ?, status = ?, attempts_json = ?, next_action = ?, updated_at = ?
                   WHERE incident_id = ? AND status = ?""",
                (terminal_fingerprint, IncidentStatus.RESOLVED.value,
                 json.dumps(attempts, sort_keys=True),
                 "authenticated ROOM confirmed absent; one bounded retry authorized",
                 timestamp, incident["incident_id"], IncidentStatus.NEEDS_HUMAN.value),
            )
            if resolved_incident.rowcount != 1:
                raise StateConflictError("authenticated absence incident resolution failed")
            self._append_event(
                connection, routine_date=day, aggregate_type="slot", aggregate_key=f"{day}:{slot}",
                from_status=SlotStatus.UNCERTAIN.value, to_status=SlotStatus.FAILED_PRE_SUBMIT.value,
                payload={"attempt_id": attempt["attempt_id"], "event": "authenticated_room_absence",
                         "evidence_source": source, "evidence_note": note, "reposted": False},
                timestamp=timestamp,
            )
            self._append_event(
                connection, routine_date=day, aggregate_type="slot", aggregate_key=f"{day}:{slot}",
                from_status=SlotStatus.FAILED_PRE_SUBMIT.value, to_status=SlotStatus.READY.value,
                payload={"event": "bounded_confirmed_absence_retry",
                         "retry": int(previous_absent) + 1,
                         "limit": DEFAULT_RETRY_BUDGET.pre_submit_retries},
                timestamp=timestamp,
            )
            self._append_event(
                connection, routine_date=day, aggregate_type="incident",
                aggregate_key=str(incident["incident_id"]),
                from_status=IncidentStatus.NEEDS_HUMAN.value,
                to_status=IncidentStatus.RESOLVED.value,
                payload={"event": "authenticated_room_absence", "evidence_source": source,
                         "root_cause_fingerprint": self._root_cause_incident_fingerprint(incident),
                         "retry_authorized": True}, timestamp=timestamp,
            )
            updated = connection.execute(
                "SELECT * FROM slots WHERE routine_date = ? AND slot = ?", (day, slot)
            ).fetchone()
        assert updated is not None
        return _slot_record(updated)

    @staticmethod
    def _terminal_incident_fingerprint(
        connection: sqlite3.Connection,
        incident: sqlite3.Row,
        target_status: IncidentStatus = IncidentStatus.RESOLVED,
    ) -> str:
        """Keep recurrent resolved incidents without dropping audit history.

        The schema's legacy UNIQUE(fingerprint, status) constraint permits one
        resolved row per root cause.  For later occurrences, the canonical root
        cause remains in the event payload and this row receives a deterministic
        terminal-only identity.  No incident row is deleted or overwritten.
        """
        fingerprint = RoomStateStore._root_cause_incident_fingerprint(incident)
        conflict = connection.execute(
            """SELECT 1 FROM incidents WHERE fingerprint = ? AND status = ?
               AND incident_id <> ? LIMIT 1""",
            (fingerprint, target_status.value, incident["incident_id"]),
        ).fetchone()
        if conflict is None:
            return fingerprint
        return hashlib.sha256(
            f"{fingerprint}\n{target_status.value}-occurrence\n{incident['incident_id']}".encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _root_cause_incident_fingerprint(incident: Mapping[str, Any]) -> str:
        return incident_fingerprint(
            ReasonCode(str(incident["reason_code"])),
            str(incident["routine_date"]),
            slot=str(incident["slot"]),
            component=str(incident["component"]),
        )

    def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def acquire_incident_lease(
        self,
        incident_id: str,
        *,
        owner: str,
        ttl_seconds: int = 300,
        now: datetime | None = None,
    ) -> bool:
        if not owner.strip() or ttl_seconds <= 0:
            raise StateStoreError("lease owner and positive ttl are required")
        timestamp = _timestamp(now)
        expires = _timestamp((now or datetime.now().astimezone()) + timedelta(seconds=ttl_seconds))
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT status, lease_owner, lease_expires_at FROM incidents WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
            if row is None:
                raise StateConflictError(f"incident does not exist: {incident_id}")
            if row["status"] in {
                IncidentStatus.RESOLVED.value,
                IncidentStatus.NEEDS_HUMAN.value,
                IncidentStatus.BUDGET_EXHAUSTED.value,
            }:
                return False
            if row["lease_owner"] and not _lease_expired(row["lease_expires_at"], timestamp):
                if row["lease_owner"] != owner:
                    return False
                connection.execute(
                    "UPDATE incidents SET lease_expires_at = ?, updated_at = ? WHERE incident_id = ? AND lease_owner = ?",
                    (expires, timestamp, incident_id, owner),
                )
                return True
            updated = connection.execute(
                """UPDATE incidents SET lease_owner = ?, lease_expires_at = ?, updated_at = ?
                   WHERE incident_id = ? AND (lease_owner IS NULL OR lease_expires_at IS NULL
                   OR lease_expires_at <= ?)""",
                (owner, expires, timestamp, incident_id, timestamp),
            )
            if updated.rowcount != 1:
                return False
            self._append_event(
                connection,
                routine_date=_incident_day(connection, incident_id),
                aggregate_type="incident",
                aggregate_key=incident_id,
                from_status=row["status"],
                to_status=row["status"],
                payload={"event": "lease_acquired", "owner": owner, "lease_expires_at": expires},
                timestamp=timestamp,
            )
            return True

    def release_incident_lease(
        self, incident_id: str, *, owner: str, now: datetime | None = None
    ) -> bool:
        timestamp = _timestamp(now)
        with self.transaction() as connection:
            updated = connection.execute(
                """UPDATE incidents SET lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                   WHERE incident_id = ? AND lease_owner = ?""",
                (timestamp, incident_id, owner),
            )
            if updated.rowcount != 1:
                return False
            row = connection.execute(
                "SELECT routine_date, status FROM incidents WHERE incident_id = ?", (incident_id,)
            ).fetchone()
            if row is not None:
                self._append_event(
                    connection,
                    routine_date=str(row["routine_date"]),
                    aggregate_type="incident",
                    aggregate_key=incident_id,
                    from_status=str(row["status"]),
                    to_status=str(row["status"]),
                    payload={"event": "lease_released", "owner": owner},
                    timestamp=timestamp,
                )
            return True

    def consume_retry_budget(
        self,
        incident_id: str,
        *,
        budget_key: str,
        budget: RetryBudget = DEFAULT_RETRY_BUDGET,
        now: datetime | None = None,
    ) -> RetryReservation:
        limits = {
            "transient_attempts": budget.transient_attempts,
            "actions_dispatches": budget.actions_dispatches,
            "artifact_fetch_attempts": budget.artifact_fetch_attempts,
            "pre_submit_retries": budget.pre_submit_retries,
            "database_restores": budget.database_restores,
            "codex_fix_cycles": budget.codex_fix_cycles,
            "actions_recovery_runs": budget.actions_recovery_runs,
        }
        if budget_key not in limits:
            raise StateStoreError(f"unknown retry budget key: {budget_key}")
        limit = limits[budget_key]
        timestamp = _timestamp(now)
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM incidents WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
            if row is None:
                raise StateConflictError(f"incident does not exist: {incident_id}")
            attempts = json.loads(str(row["attempts_json"] or "{}"))
            current = int(attempts.get(budget_key, 0))
            if current >= limit:
                if row["status"] not in {
                    IncidentStatus.RESOLVED.value,
                    IncidentStatus.NEEDS_HUMAN.value,
                    IncidentStatus.BUDGET_EXHAUSTED.value,
                }:
                    validate_incident_transition(row["status"], IncidentStatus.BUDGET_EXHAUSTED)
                    terminal_fingerprint = self._terminal_incident_fingerprint(
                        connection, row, IncidentStatus.BUDGET_EXHAUSTED
                    )
                    connection.execute(
                        "UPDATE incidents SET fingerprint = ?, status = ?, next_action = ?, updated_at = ? WHERE incident_id = ?",
                        (terminal_fingerprint, IncidentStatus.BUDGET_EXHAUSTED.value,
                         "stop automatic retries", timestamp, incident_id),
                    )
                    self._append_event(
                        connection,
                        routine_date=str(row["routine_date"]),
                        aggregate_type="incident",
                        aggregate_key=incident_id,
                        from_status=str(row["status"]),
                        to_status=IncidentStatus.BUDGET_EXHAUSTED.value,
                        payload={"budget_key": budget_key, "limit": limit,
                                 "root_cause_fingerprint": self._root_cause_incident_fingerprint(row)},
                        timestamp=timestamp,
                    )
                return RetryReservation(incident_id, budget_key, current, limit, True)
            attempt = current + 1
            attempts[budget_key] = attempt
            connection.execute(
                "UPDATE incidents SET attempts_json = ?, updated_at = ? WHERE incident_id = ?",
                (json.dumps(attempts, sort_keys=True), timestamp, incident_id),
            )
            self._append_event(
                connection,
                routine_date=str(row["routine_date"]),
                aggregate_type="incident",
                aggregate_key=incident_id,
                from_status=str(row["status"]),
                to_status=str(row["status"]),
                payload={"event": "retry_reserved", "budget_key": budget_key, "attempt": attempt, "limit": limit},
                timestamp=timestamp,
            )
            return RetryReservation(incident_id, budget_key, attempt, limit, attempt >= limit)

    def reclaim_pre_submit_budget(self, incident_id: str, *, budget_key: str = "pre_submit_retries", now: datetime | None = None) -> None:
        """Reclaim a retry consumed by a proven browser-startup failure only."""
        timestamp = _timestamp(now)
        with self.transaction() as connection:
            row = connection.execute("SELECT routine_date, slot, status, attempts_json FROM incidents WHERE incident_id = ?", (incident_id,)).fetchone()
            if row is None or row["status"] != IncidentStatus.AUTO_RECOVERING.value:
                raise StateConflictError("incident is not an auto-recovering pre-submit incident")
            attempts = json.loads(str(row["attempts_json"] or "{}"))
            current = int(attempts.get(budget_key, 0))
            if current < 1:
                raise StateConflictError("no retry budget is available to reclaim")
            unsafe = connection.execute(
                "SELECT COUNT(*) AS count FROM post_attempts WHERE routine_date = ? AND slot = ? AND submit_started = 1",
                (row["routine_date"], row["slot"]),
            ).fetchone()["count"]
            if int(unsafe):
                raise StateConflictError("cannot reclaim budget after submit started")
            attempts[budget_key] = current - 1
            connection.execute("UPDATE incidents SET attempts_json = ?, updated_at = ? WHERE incident_id = ?", (json.dumps(attempts, sort_keys=True), timestamp, incident_id))
            self._append_event(connection, routine_date=str(row["routine_date"]), aggregate_type="incident", aggregate_key=incident_id, from_status=row["status"], to_status=row["status"], payload={"event": "pre_submit_budget_reclaimed", "budget_key": budget_key}, timestamp=timestamp)

    def accept_manifest(self, payload: Mapping[str, Any], *, now: datetime | None = None) -> int:
        validate_manifest_v2(payload)
        day = str(payload["routine_date_jst"])
        revision = int(payload["revision"])
        manifest_hash = hashlib.sha256(
            json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        timestamp = _timestamp(now)
        with self.transaction() as connection:
            existing_hash = connection.execute(
                "SELECT revision FROM manifests WHERE manifest_hash = ?", (manifest_hash,)
            ).fetchone()
            if existing_hash is not None:
                return int(existing_hash["revision"])
            latest = connection.execute(
                "SELECT MAX(revision) AS revision FROM manifests WHERE routine_date = ?", (day,)
            ).fetchone()["revision"]
            if latest is not None and revision <= int(latest):
                raise StateConflictError(f"manifest revision is not newer: {revision} <= {latest}")
            connection.execute(
                "INSERT INTO manifests(routine_date, revision, manifest_hash, actions_run_id, head_sha, payload_json, accepted_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (day, revision, manifest_hash, str(payload["actions_run_id"]), str(payload["head_sha"]), json.dumps(dict(payload), ensure_ascii=False, sort_keys=True), timestamp),
            )
            self._ensure_day(connection, day, timestamp)
            for slot in POST_SLOTS:
                current = connection.execute(
                    "SELECT * FROM slots WHERE routine_date = ? AND slot = ?", (day, slot)
                ).fetchone()
                if current is not None and str(current["status"]) in {
                    SlotStatus.CLAIMED.value, SlotStatus.SUBMITTING.value,
                    SlotStatus.SUBMITTED_UNCONFIRMED.value, SlotStatus.UNCERTAIN.value,
                    SlotStatus.POSTED.value,
                }:
                    continue
                value = payload["slots"][slot]
                target = SlotStatus.READY if value["status"] == "ready" else SlotStatus.BLOCKED
                candidate = value.get("candidate") or {}
                normalized = normalize_product_url(str(candidate.get("normalized_url", ""))) if candidate else ""
                content_hash = str(candidate.get("content_hash", "")) if candidate else ""
                product_type = str(candidate.get("product_type", "")) if candidate else ""
                if current is None:
                    connection.execute(
                        "INSERT INTO slots(routine_date, slot, status, manifest_revision, normalized_url, content_hash, product_type, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (day, slot, target.value, revision, normalized, content_hash, product_type, timestamp),
                    )
                    old = None
                else:
                    old = str(current["status"])
                    connection.execute(
                        "UPDATE slots SET status = ?, manifest_revision = ?, normalized_url = ?, content_hash = ?, product_type = ?, version = version + 1, updated_at = ? WHERE routine_date = ? AND slot = ?",
                        (target.value, revision, normalized, content_hash, product_type, timestamp, day, slot),
                    )
                self._append_event(connection, routine_date=day, aggregate_type="slot", aggregate_key=f"{day}:{slot}", from_status=old, to_status=target.value, payload={"manifest_revision": revision}, timestamp=timestamp)
            return revision

    def expire_previous_days(self, before: date | str, *, now: datetime | None = None) -> dict[str, int]:
        cutoff = _date_text(before)
        timestamp = _timestamp(now)
        expirable = {
            SlotStatus.PENDING.value, SlotStatus.READY.value, SlotStatus.CLAIMED.value,
            SlotStatus.FAILED_PRE_SUBMIT.value, SlotStatus.BLOCKED.value,
        }
        expired = 0
        held = 0
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT routine_date, slot, status, version FROM slots WHERE routine_date < ?", (cutoff,)
            ).fetchall()
            for row in rows:
                status = str(row["status"])
                if status in expirable:
                    connection.execute(
                        "UPDATE slots SET status = ?, version = version + 1, updated_at = ? WHERE routine_date = ? AND slot = ? AND version = ?",
                        (SlotStatus.EXPIRED_UNPOSTED.value, timestamp, row["routine_date"], row["slot"], row["version"]),
                    )
                    self._append_event(connection, routine_date=str(row["routine_date"]), aggregate_type="slot", aggregate_key=f"{row['routine_date']}:{row['slot']}", from_status=status, to_status=SlotStatus.EXPIRED_UNPOSTED.value, payload={"boundary": cutoff}, timestamp=timestamp)
                    expired += 1
                elif status not in {SlotStatus.POSTED.value, SlotStatus.EXPIRED_UNPOSTED.value}:
                    held += 1
            incidents = connection.execute(
                "SELECT * FROM incidents WHERE routine_date < ? AND status IN ('open','auto_recovering','codex_queued','codex_working','validating')",
                (cutoff,),
            ).fetchall()
            for row in incidents:
                validate_incident_transition(row["status"], IncidentStatus.NEEDS_HUMAN)
                terminal_fingerprint = self._terminal_incident_fingerprint(
                    connection, row, IncidentStatus.NEEDS_HUMAN
                )
                connection.execute(
                    "UPDATE incidents SET fingerprint = ?, status = ?, next_action = ?, updated_at = ? WHERE incident_id = ?",
                    (terminal_fingerprint, IncidentStatus.NEEDS_HUMAN.value,
                     "manual review after routine-day boundary", timestamp, row["incident_id"]),
                )
                self._append_event(connection, routine_date=str(row["routine_date"]), aggregate_type="incident", aggregate_key=str(row["incident_id"]), from_status=str(row["status"]), to_status=IncidentStatus.NEEDS_HUMAN.value, payload={"boundary": cutoff, "root_cause_fingerprint": self._root_cause_incident_fingerprint(row)}, timestamp=timestamp)
        return {"expired_slots": expired, "held_slots": held, "expired_incidents": len(incidents)}

    def build_catch_up_plan(self, today: date | str) -> CatchUpPlan:
        day = _date_text(today)
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT routine_date, slot, status FROM slots WHERE routine_date < ? AND status NOT IN ('posted','expired_unposted') ORDER BY routine_date, slot",
                (day,),
            ).fetchall()
            today_rows = {str(row["slot"]): str(row["status"]) for row in connection.execute("SELECT slot, status FROM slots WHERE routine_date = ?", (day,)).fetchall()}
        expired = tuple(f"{row['routine_date']}:{row['slot']}" for row in rows if row["status"] != SlotStatus.UNCERTAIN.value)
        human = tuple(f"{row['routine_date']}:{row['slot']}" for row in rows if row["status"] == SlotStatus.UNCERTAIN.value)
        missing = tuple(slot for slot in POST_SLOTS if slot not in today_rows)
        return CatchUpPlan(expired, human, missing)
    def import_legacy_ledger(
        self,
        path: Path | str,
        *,
        now: datetime | None = None,
    ) -> LegacyImportResult:
        source = Path(path)
        timestamp = _timestamp(now)
        imported = 0
        duplicates = 0
        malformed = 0
        latest_by_slot: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
        new_lines: list[tuple[str, int]] = []

        if not source.exists():
            return LegacyImportResult(0, 0, 0, 0)

        with closing(self.connect()) as connection:
            known_hashes = {
                str(row[0])
                for row in connection.execute("SELECT line_hash FROM legacy_imports").fetchall()
            }

        for line_number, raw_line in enumerate(
            source.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not raw_line.strip():
                continue
            line_hash = hashlib.sha256(raw_line.encode("utf-8")).hexdigest()
            if line_hash in known_hashes:
                duplicates += 1
                continue
            try:
                event = json.loads(raw_line)
                day, slot = _split_legacy_slot(str(event.get("post_slot", "")))
                legacy_status = str(event.get("status", ""))
                if legacy_status not in {"reserved", "posted", "failed"}:
                    raise ValueError("unsupported legacy status")
            except (json.JSONDecodeError, ValueError, StateStoreError):
                malformed += 1
                continue
            new_lines.append((line_hash, line_number))
            latest_by_slot[(day, slot)] = (line_number, event)

        with self.transaction() as connection:
            for line_hash, line_number in new_lines:
                connection.execute(
                    """
                    INSERT INTO legacy_imports(line_hash, source_path, source_line, imported_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (line_hash, str(source), line_number, timestamp),
                )
                imported += 1

            for (day, slot), (_line_number, event) in latest_by_slot.items():
                self._ensure_day(connection, day, timestamp)
                target = _legacy_slot_status(event)
                raw_url = str(event.get("normalized_url", "")).strip()
                normalized_url = normalize_product_url(raw_url) if raw_url else ""
                existing = connection.execute(
                    "SELECT * FROM slots WHERE routine_date = ? AND slot = ?",
                    (day, slot),
                ).fetchone()
                if existing is not None and existing["status"] == SlotStatus.POSTED.value:
                    continue
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO slots(
                            routine_date, slot, status, normalized_url,
                            product_type, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            day,
                            slot,
                            target.value,
                            normalized_url,
                            str(event.get("product_type", ""))[:80],
                            timestamp,
                        ),
                    )
                    from_status = None
                else:
                    connection.execute(
                        """
                        UPDATE slots
                        SET status = ?, normalized_url = CASE WHEN ? <> '' THEN ? ELSE normalized_url END,
                            product_type = CASE WHEN ? <> '' THEN ? ELSE product_type END,
                            version = version + 1, updated_at = ?
                        WHERE routine_date = ? AND slot = ?
                        """,
                        (
                            target.value,
                            normalized_url,
                            normalized_url,
                            str(event.get("product_type", ""))[:80],
                            str(event.get("product_type", ""))[:80],
                            timestamp,
                            day,
                            slot,
                        ),
                    )
                    from_status = str(existing["status"])
                self._append_event(
                    connection,
                    routine_date=day,
                    aggregate_type="slot",
                    aggregate_key=f"{day}:{slot}",
                    from_status=from_status,
                    to_status=target.value,
                    payload={"source": "legacy_ledger_import"},
                    timestamp=timestamp,
                )

        return LegacyImportResult(imported, duplicates, malformed, len(latest_by_slot))

    def sync_legacy_ledger(
        self,
        path: Path | str,
        *,
        routine_date: date | str | None = None,
        now: datetime | None = None,
    ) -> LegacySyncResult:
        """Append a rollback-compatible projection without overwriting the old ledger."""
        source = Path(path)
        source.parent.mkdir(parents=True, exist_ok=True)
        timestamp = _timestamp(now)
        day_filter = _date_text(routine_date) if routine_date is not None else None
        with closing(self.connect()) as connection:
            query = "SELECT * FROM slots WHERE status IN ('claimed','submitting','submitted_unconfirmed','uncertain','posted','failed_pre_submit')"
            params: tuple[Any, ...] = ()
            if day_filter is not None:
                query += " AND routine_date = ?"
                params = (day_filter,)
            rows = connection.execute(query + " ORDER BY routine_date, slot", params).fetchall()
        existing_lines = set(source.read_text(encoding="utf-8").splitlines()) if source.exists() else set()
        pending: list[str] = []
        for row in rows:
            status = str(row["status"])
            legacy_status = "posted" if status == SlotStatus.POSTED.value else "failed" if status == SlotStatus.FAILED_PRE_SUBMIT.value else "reserved"
            event = {
                "post_slot": f"{row['routine_date']}:{row['slot']}",
                "status": legacy_status,
                "normalized_url": str(row["normalized_url"]),
                "product_type": str(row["product_type"]),
                "source": "state_store_v2_compat",
            }
            line = json.dumps(event, ensure_ascii=False, sort_keys=True)
            if line not in existing_lines:
                pending.append(line)
                existing_lines.add(line)
        if pending:
            with source.open("a", encoding="utf-8", newline="\n") as handle:
                for line in pending:
                    handle.write(line + "\n")
        return LegacySyncResult(len(pending), len(rows) - len(pending), 0)

    def export_snapshot(self, routine_date: date | str) -> dict[str, Any]:
        day = _date_text(routine_date)
        with closing(self.connect()) as connection:
            day_row = connection.execute(
                "SELECT * FROM days WHERE routine_date = ?", (day,)
            ).fetchone()
            slots = connection.execute(
                "SELECT * FROM slots WHERE routine_date = ? ORDER BY slot", (day,)
            ).fetchall()
            incidents = connection.execute(
                """
                SELECT incident_id, fingerprint, routine_date, slot, component, reason_code,
                       route, status, last_safe_state, manifest_revision,
                       attempts_json, next_action, created_at, updated_at
                FROM incidents WHERE routine_date = ? ORDER BY created_at
                """,
                (day,),
            ).fetchall()
        return {
            "schema_version": DATABASE_SCHEMA_VERSION,
            "routine_date_jst": day,
            "day_status": str(day_row["status"]) if day_row is not None else "missing",
            "slots": {
                str(row["slot"]): {
                    "status": str(row["status"]),
                    "manifest_revision": row["manifest_revision"],
                    "normalized_url": str(row["normalized_url"]),
                    "content_hash": str(row["content_hash"]),
                    "product_type": str(row["product_type"]),
                    "version": int(row["version"]),
                    "updated_at": str(row["updated_at"]),
                }
                for row in slots
            },
            "incidents": [
                {
                    **dict(row),
                    "root_cause_fingerprint": self._root_cause_incident_fingerprint(row),
                }
                for row in incidents
            ],
        }

    @staticmethod
    def _ensure_day(connection: sqlite3.Connection, day: str, timestamp: str) -> None:
        connection.execute(
            """
            INSERT INTO days(routine_date, created_at, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(routine_date) DO UPDATE SET updated_at = excluded.updated_at
            """,
            (day, timestamp, timestamp),
        )

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        *,
        routine_date: str,
        aggregate_type: str,
        aggregate_key: str,
        from_status: str | None,
        to_status: str,
        payload: Mapping[str, Any],
        timestamp: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO events(
                event_id, routine_date, aggregate_type, aggregate_key,
                from_status, to_status, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid4().hex,
                routine_date,
                aggregate_type,
                aggregate_key,
                from_status,
                to_status,
                json.dumps(dict(payload), ensure_ascii=False, sort_keys=True),
                timestamp,
            ),
        )


def _slot_record(row: sqlite3.Row) -> SlotRecord:
    return SlotRecord(
        routine_date=str(row["routine_date"]),
        slot=str(row["slot"]),
        status=SlotStatus(str(row["status"])),
        manifest_revision=row["manifest_revision"],
        normalized_url=str(row["normalized_url"]),
        content_hash=str(row["content_hash"]),
        product_type=str(row["product_type"]),
        version=int(row["version"]),
        updated_at=str(row["updated_at"]),
    )


def _legacy_slot_status(event: Mapping[str, Any]) -> SlotStatus:
    status = str(event.get("status", ""))
    if status == "posted":
        return SlotStatus.POSTED
    if status == "failed" and str(event.get("detail", "")) in DEFINITIVE_LEGACY_PRE_SUBMIT_FAILURES:
        return SlotStatus.FAILED_PRE_SUBMIT
    return SlotStatus.UNCERTAIN


def _split_legacy_slot(value: str) -> tuple[str, str]:
    day, separator, slot = value.partition(":")
    if not separator:
        raise ValueError("legacy post_slot must contain date and slot")
    _date_text(day)
    _validate_slot(slot)
    return day, slot


def _date_text(value: date | str) -> str:
    text = value.isoformat() if isinstance(value, date) else str(value)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise StateStoreError(f"invalid routine date: {text}") from exc
    if parsed.isoformat() != text:
        raise StateStoreError(f"routine date must use YYYY-MM-DD: {text}")
    return text


def _validate_slot(slot: str) -> None:
    if slot not in POST_SLOTS:
        raise StateStoreError(f"unknown post slot: {slot}")


def _timestamp(now: datetime | None) -> str:
    value = now or datetime.now().astimezone()
    if value.tzinfo is None or value.utcoffset() is None:
        raise StateStoreError("timestamp requires a timezone-aware datetime")
    return value.isoformat()


def _lease_expired(value: str | None, timestamp: str) -> bool:
    if not value:
        return True
    try:
        return datetime.fromisoformat(value) <= datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise StateIntegrityError("invalid incident lease timestamp") from exc


def _lease_is_owned(row: sqlite3.Row | None, owner: str, timestamp: str) -> bool:
    return bool(
        row is not None
        and row["lease_owner"] == owner
        and row["lease_expires_at"]
        and not _lease_expired(row["lease_expires_at"], timestamp)
    )


def _incident_day(connection: sqlite3.Connection, incident_id: str) -> str:
    row = connection.execute(
        "SELECT routine_date FROM incidents WHERE incident_id = ?", (incident_id,)
    ).fetchone()
    if row is None:
        raise StateConflictError(f"incident does not exist: {incident_id}")
    return str(row["routine_date"])
