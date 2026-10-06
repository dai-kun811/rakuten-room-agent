from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import date, datetime
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
    normalize_product_url,
    route_for_reason,
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
"""


class RoomStateStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
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
                WHERE fingerprint = ? AND status NOT IN ('resolved', 'needs_human', 'budget_exhausted')
                ORDER BY created_at DESC LIMIT 1
                """,
                (fingerprint,),
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
        now: datetime | None = None,
    ) -> None:
        validate_incident_transition(expected_status, target_status)
        timestamp = _timestamp(now)
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT routine_date, status FROM incidents WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
            if row is None or row["status"] != expected_status.value:
                actual = "missing" if row is None else row["status"]
                raise StateConflictError(
                    f"stale incident state: expected={expected_status.value} actual={actual}"
                )
            connection.execute(
                """
                UPDATE incidents SET status = ?, next_action = ?, updated_at = ?
                WHERE incident_id = ? AND status = ?
                """,
                (
                    target_status.value,
                    next_action,
                    timestamp,
                    incident_id,
                    expected_status.value,
                ),
            )
            self._append_event(
                connection,
                routine_date=row["routine_date"],
                aggregate_type="incident",
                aggregate_key=incident_id,
                from_status=expected_status.value,
                to_status=target_status.value,
                payload={"next_action": next_action},
                timestamp=timestamp,
            )

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
            except (json.JSONDecodeError, ValueError):
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
                SELECT incident_id, fingerprint, slot, component, reason_code,
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
            "incidents": [dict(row) for row in incidents],
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
