from __future__ import annotations

import json
import hashlib
import sqlite3
import sys
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import room_engagement_verifier as engagement_verifier
from room_engagement_verifier import EXPECTED_POLICY_ID, verify_engagement


DAY = "2026-11-16"


class EngagementVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "operations-copy.db"
        self.ledger = self.root / "engagement-ledger.jsonl"
        self.summary = self.root / "daily-routine-summary.json"
        self.heartbeat = self.root / "engagement-heartbeat.json"
        self.log = self.root / "engagement-worker.log"
        self._create_schema()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _create_schema(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executescript(
                """
                CREATE TABLE engagement_runs (
                    routine_date TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    goal INTEGER NOT NULL,
                    followed INTEGER NOT NULL,
                    liked INTEGER NOT NULL,
                    owner_id TEXT,
                    last_error_code TEXT
                );
                CREATE TABLE engagement_actions (
                    routine_date TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    room_url TEXT NOT NULL,
                    followed INTEGER NOT NULL,
                    liked INTEGER NOT NULL,
                    follow_status TEXT NOT NULL,
                    like_status TEXT NOT NULL,
                    PRIMARY KEY (routine_date, candidate_id)
                );
                """
            )
            connection.commit()

    def _write_common_evidence(self, *, failures: int = 0, error_line: bool = False) -> None:
        self.summary.write_text(
            json.dumps(
                {
                    "routine_date": DAY,
                    "policy_id": EXPECTED_POLICY_ID,
                    "goal": 15,
                    "followed": 15,
                    "liked": 15,
                    "attempted": 15,
                    "failures": failures,
                    "completed": True,
                    "status": "completed",
                    "error_code": "",
                    "published": True,
                }
            ),
            encoding="utf-8",
        )
        self.heartbeat.write_text(
            json.dumps(
                {
                    "role": "engagement",
                    "routine_date": DAY,
                    "status": "completed",
                    "updated_at": f"{DAY}T05:50:00+09:00",
                }
            ),
            encoding="utf-8",
        )
        log_lines = [
            f"{DAY} 05:49:59,000 INFO Engagement progress candidate=room_14 follow=15/15 like=15/15",
            f"{DAY} 05:50:00,000 INFO Public routine progress updated day={DAY} follow=15 like=15 completed=True",
        ]
        if error_line:
            log_lines.insert(0, f"{DAY} 05:40:00,000 ERROR Paired latest-post like did not complete")
        self.log.write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    def _add_complete_pairs(self, count: int = 15) -> None:
        events: list[dict[str, object]] = []
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "INSERT INTO engagement_runs VALUES(?,?,?,?,?,?,?)",
                (DAY, "completed", 15, count, count, None, ""),
            )
            for index in range(count):
                candidate_id = f"room_{index}"
                room_url = f"https://room.rakuten.co.jp/{candidate_id}/items"
                connection.execute(
                    "INSERT INTO engagement_actions VALUES(?,?,?,?,?,?,?)",
                    (DAY, candidate_id, room_url, 1, 1, "followed", "liked"),
                )
                events.append(
                    {
                        "timestamp": f"{DAY}T05:40:00+09:00",
                        "routine_date": DAY,
                        "candidate_id": candidate_id,
                        "room_url": room_url,
                        "followed": True,
                        "liked": True,
                        "follow_status": "followed",
                        "like_status": "liked",
                    }
                )
            connection.commit()
        self.ledger.write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )

    def _verify(self, *, require_clean_log: bool = False) -> dict[str, object]:
        return verify_engagement(
            database_path=self.database,
            ledger_path=self.ledger,
            summary_path=self.summary,
            heartbeat_path=self.heartbeat,
            log_path=self.log,
            routine_date=DAY,
            require_clean_log=require_clean_log,
        )

    def test_accepts_exact_clean_fifteen_pairs(self) -> None:
        self._add_complete_pairs()
        self._write_common_evidence()
        before_hash = hashlib.sha256(self.database.read_bytes()).hexdigest()

        report = self._verify(require_clean_log=True)

        self.assertTrue(report["ok"])
        self.assertTrue(report["clean_run"])
        self.assertEqual(report["database"]["paired_count"], 15)
        self.assertEqual(report["ledger"]["new_follow_evidence_count"], 15)
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), before_hash)

    def test_cli_refuses_canonical_live_database(self) -> None:
        self._add_complete_pairs()
        self._write_common_evidence()
        arguments = [
            "room_engagement_verifier.py",
            "--database",
            str(self.database),
            "--ledger",
            str(self.ledger),
            "--summary",
            str(self.summary),
            "--heartbeat",
            str(self.heartbeat),
            "--log",
            str(self.log),
            "--routine-date",
            DAY,
        ]

        with patch.object(engagement_verifier, "LIVE_DATABASE_PATH", self.database), \
             patch.object(sys, "argv", arguments), \
             self.assertRaises(SystemExit) as raised:
            engagement_verifier.main()

        self.assertEqual(raised.exception.code, 2)

    def test_rejects_split_follow_and_like_sets(self) -> None:
        events: list[dict[str, object]] = []
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "INSERT INTO engagement_runs VALUES(?,?,?,?,?,?,?)",
                (DAY, "completed", 15, 15, 15, None, ""),
            )
            for index in range(30):
                candidate_id = f"room_{index}"
                followed = index < 15
                liked = index >= 15
                room_url = f"https://room.rakuten.co.jp/{candidate_id}/items"
                connection.execute(
                    "INSERT INTO engagement_actions VALUES(?,?,?,?,?,?,?)",
                    (
                        DAY,
                        candidate_id,
                        room_url,
                        int(followed),
                        int(liked),
                        "followed" if followed else "goal_reached",
                        "liked" if liked else "not_new_follow",
                    ),
                )
                events.append(
                    {
                        "routine_date": DAY,
                        "candidate_id": candidate_id,
                        "room_url": room_url,
                        "followed": followed,
                        "liked": liked,
                        "follow_status": "followed" if followed else "goal_reached",
                        "like_status": "liked" if liked else "not_new_follow",
                    }
                )
            connection.commit()
        self.ledger.write_text(
            "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
        )
        self._write_common_evidence()

        report = self._verify()

        self.assertFalse(report["ok"])
        self.assertIn("db_pair_set_mismatch", report["errors"])

    def test_rejects_already_following_candidate_counted_as_pair(self) -> None:
        self._add_complete_pairs()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """UPDATE engagement_actions SET follow_status='already_following'
                   WHERE routine_date=? AND candidate_id='room_0'""",
                (DAY,),
            )
            connection.commit()
        self._write_common_evidence()

        report = self._verify()

        self.assertFalse(report["ok"])
        self.assertIn("already_following_counted", report["errors"])

    def test_accepts_same_candidate_resume_with_historical_new_follow_evidence(self) -> None:
        self._add_complete_pairs()
        events = [json.loads(line) for line in self.ledger.read_text(encoding="utf-8").splitlines()]
        resumed = events[0]
        resumed["liked"] = False
        resumed["like_status"] = "like_exception_TimeoutError"
        events.insert(
            1,
            {
                **events[0],
                "followed": True,
                "liked": True,
                "follow_status": "paired_follow_already_recorded",
                "like_status": "liked",
            },
        )
        self.ledger.write_text(
            "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
        )
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """UPDATE engagement_actions
                   SET follow_status='paired_follow_already_recorded', like_status='liked'
                   WHERE routine_date=? AND candidate_id='room_0'""",
                (DAY,),
            )
            connection.commit()
        self._write_common_evidence(error_line=True)

        report = self._verify()

        self.assertTrue(report["ok"])
        self.assertFalse(report["clean_run"])
        self.assertIn("log_errors_present", report["warnings"])

    def test_clean_log_gate_rejects_recovered_error_history(self) -> None:
        self._add_complete_pairs()
        self._write_common_evidence(error_line=True)

        report = self._verify(require_clean_log=True)

        self.assertFalse(report["ok"])
        self.assertIn("clean_log_required", report["errors"])


if __name__ == "__main__":
    unittest.main()
