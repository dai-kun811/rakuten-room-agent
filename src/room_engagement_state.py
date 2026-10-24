"""SQLite-backed state and safety gates for the daily engagement routine."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, time
from pathlib import Path
from room_operation_contract import JST
ENGAGEMENT_START = time(5, 30)
ENGAGEMENT_CUTOFF = time(7, 30)


def _now(value: datetime | None = None) -> datetime:
    return (value or datetime.now(tz=JST)).astimezone(JST)


def _stamp(value: datetime | None = None) -> str:
    return _now(value).isoformat()


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS engagement_runs (
            routine_date TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            goal INTEGER NOT NULL,
            followed INTEGER NOT NULL DEFAULT 0,
            liked INTEGER NOT NULL DEFAULT 0,
            owner_id TEXT,
            last_candidate_id TEXT,
            last_error_code TEXT,
            started_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS engagement_actions (
            routine_date TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            room_url TEXT NOT NULL,
            followed INTEGER NOT NULL DEFAULT 0,
            liked INTEGER NOT NULL DEFAULT 0,
            follow_status TEXT NOT NULL DEFAULT '',
            like_status TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (routine_date, candidate_id)
        );
        """
    )


def _connect(path: Path | str) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    ensure_schema(connection)
    return connection


def start_gate(path: Path | str, *, now: datetime | None = None, goal: int = 50, owner: str = "engagement") -> dict:
    """Claim the one daily engagement run, without touching a browser before the gate."""
    local = _now(now)
    day = local.date().isoformat()
    current = local.time().replace(tzinfo=None)
    if current < ENGAGEMENT_START:
        return {"status": "not_due", "routine_date": day, "reason": "before_start"}
    if current >= ENGAGEMENT_CUTOFF:
        stamp = _stamp(local)
        with closing(_connect(path)) as connection:
            connection.execute(
                """INSERT INTO engagement_runs(routine_date,status,goal,started_at,updated_at)
                   VALUES(?,?,?,?,?) ON CONFLICT(routine_date) DO NOTHING""",
                (day, "skipped", goal, stamp, stamp),
            )
            connection.commit()
        return {"status": "skipped", "routine_date": day, "reason": "cutoff_expired"}
    stamp = _stamp(local)
    with closing(_connect(path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM engagement_runs WHERE routine_date = ?", (day,)).fetchone()
        if row is not None and row["status"] == "completed":
            connection.commit()
            return {"status": "already_complete", "routine_date": day, "followed": row["followed"], "liked": row["liked"]}
        if row is not None and row["status"] == "running" and row["owner_id"] not in {None, owner}:
            connection.commit()
            return {"status": "busy", "routine_date": day, "reason": "another_owner"}
        connection.execute(
            """INSERT INTO engagement_runs(routine_date,status,goal,owner_id,started_at,updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(routine_date) DO UPDATE SET status='running', goal=excluded.goal,
                 owner_id=excluded.owner_id, updated_at=excluded.updated_at""",
            (day, "running", goal, owner, stamp, stamp),
        )
        connection.commit()
    return {"status": "allowed", "routine_date": day, "goal": goal}


def record_action(path: Path | str, *, routine_date: str, candidate_id: str, room_url: str,
                  followed: bool, liked: bool, follow_status: str, like_status: str,
                  now: datetime | None = None) -> None:
    stamp = _stamp(now)
    with closing(_connect(path)) as connection:
        connection.execute(
            """INSERT INTO engagement_actions(
                 routine_date,candidate_id,room_url,followed,liked,follow_status,like_status,updated_at)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(routine_date,candidate_id) DO UPDATE SET
                 room_url=excluded.room_url, followed=MAX(engagement_actions.followed,excluded.followed),
                 liked=MAX(engagement_actions.liked,excluded.liked), follow_status=excluded.follow_status,
                 like_status=excluded.like_status, updated_at=excluded.updated_at""",
            (routine_date, candidate_id, room_url, int(followed), int(liked), follow_status, like_status, stamp),
        )
        counts = connection.execute(
            "SELECT COALESCE(SUM(followed),0), COALESCE(SUM(liked),0) FROM engagement_actions WHERE routine_date=?",
            (routine_date,),
        ).fetchone()
        connection.execute(
            "UPDATE engagement_runs SET followed=?, liked=?, last_candidate_id=?, updated_at=? WHERE routine_date=?",
            (int(counts[0]), int(counts[1]), candidate_id, stamp, routine_date),
        )
        connection.commit()


def progress(path: Path | str, routine_date: str) -> dict[str, dict[str, bool]]:
    with closing(_connect(path)) as connection:
        rows = connection.execute(
            "SELECT candidate_id,followed,liked FROM engagement_actions WHERE routine_date=?", (routine_date,)
        ).fetchall()
    return {str(row["candidate_id"]): {"followed": bool(row["followed"]), "liked": bool(row["liked"])} for row in rows}


def finish(path: Path | str, *, routine_date: str, followed: int, liked: int, goal: int,
           status: str, error_code: str = "", now: datetime | None = None) -> None:
    stamp = _stamp(now)
    with closing(_connect(path)) as connection:
        connection.execute(
            """UPDATE engagement_runs SET status=?, followed=?, liked=?, last_error_code=?,
               owner_id=NULL, updated_at=? WHERE routine_date=?""",
            (status, followed, liked, error_code, stamp, routine_date),
        )
        connection.commit()


def audit(path: Path | str, *, routine_date: str, now: datetime | None = None, stale_minutes: int = 180) -> dict:
    local = _now(now)
    with closing(_connect(path)) as connection:
        run = connection.execute("SELECT * FROM engagement_runs WHERE routine_date=?", (routine_date,)).fetchone()
        action_count = connection.execute("SELECT COUNT(*) FROM engagement_actions WHERE routine_date=?", (routine_date,)).fetchone()[0]
    errors: list[str] = []
    if run is None:
        if local.time().replace(tzinfo=None) >= ENGAGEMENT_CUTOFF:
            errors.append("engagement_run_missing")
        return {"routine_date": routine_date, "ok": not errors, "errors": errors, "action_count": action_count}
    if run["status"] == "running":
        updated = datetime.fromisoformat(str(run["updated_at"]))
        if (local - updated.astimezone(JST)).total_seconds() > stale_minutes * 60:
            errors.append("engagement_heartbeat_stale")
    if run["status"] not in {"completed", "skipped", "not_due"} and local.time().replace(tzinfo=None) >= ENGAGEMENT_CUTOFF:
        errors.append("engagement_incomplete")
    return {"routine_date": routine_date, "ok": not errors, "errors": errors, "status": run["status"],
            "followed": int(run["followed"]), "liked": int(run["liked"]), "action_count": action_count}


def sanitized_heartbeat(path: Path | str, *, role: str, status: str, routine_date: str,
                       now: datetime | None = None, error_code: str = "") -> dict:
    """Return only operational metadata; never include URL, product, or copy text."""
    payload = {"role": role, "status": status, "routine_date": routine_date,
               "updated_at": _stamp(now)}
    if error_code:
        payload["error_code"] = error_code
    heartbeat_path = Path(path)
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return payload
