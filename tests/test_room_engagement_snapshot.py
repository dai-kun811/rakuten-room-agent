from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import room_engagement_snapshot as snapshot_module
from room_engagement_snapshot import EVIDENCE_FILES, SnapshotError, create_snapshot
from room_engagement_verifier import EXPECTED_POLICY_ID, verify_engagement


class EngagementSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.output = self.root / "observation"
        self.source.mkdir()
        for index, name in enumerate(EVIDENCE_FILES):
            (self.source / name).write_bytes(f"evidence-{index}\n".encode())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_creates_consistent_five_file_snapshot_and_manifest(self) -> None:
        manifest = create_snapshot(source_dir=self.source, output_dir=self.output)

        self.assertTrue(manifest["consistent"])
        self.assertFalse(manifest["source_database_opened"])
        self.assertEqual(set(manifest["files"]), set(EVIDENCE_FILES))
        for name in EVIDENCE_FILES:
            self.assertEqual((self.output / name).read_bytes(), (self.source / name).read_bytes())
            self.assertTrue(manifest["files"][name]["consistent"])
        saved = json.loads(
            (self.output / "snapshot-manifest.json").read_text(encoding="utf-8")
        )
        self.assertTrue(saved["consistent"])
        self.assertEqual(saved["files"]["operations.db"]["source_before"]["sha256"],
                         saved["files"]["operations.db"]["copy"]["sha256"])

    def test_refuses_snapshot_when_wal_exists(self) -> None:
        (self.source / "operations.db-wal").write_bytes(b"pending")

        with self.assertRaisesRegex(SnapshotError, "database_auxiliary_present"):
            create_snapshot(source_dir=self.source, output_dir=self.output)

        self.assertFalse(self.output.exists())

    def test_refuses_snapshot_when_shm_exists(self) -> None:
        (self.source / "operations.db-shm").write_bytes(b"active-reader")

        with self.assertRaisesRegex(SnapshotError, "database_auxiliary_present"):
            create_snapshot(source_dir=self.source, output_dir=self.output)

        self.assertFalse(self.output.exists())

    def test_refuses_snapshot_when_required_evidence_is_missing(self) -> None:
        (self.source / "engagement-heartbeat.json").unlink()

        with self.assertRaisesRegex(SnapshotError, "snapshot_source_missing"):
            create_snapshot(source_dir=self.source, output_dir=self.output)

        self.assertFalse(self.output.exists())

    def test_marks_snapshot_inconsistent_when_source_changes_during_copy(self) -> None:
        real_copy = snapshot_module.shutil.copy2
        changed = False

        def copy_and_change(source: Path, destination: Path):
            nonlocal changed
            result = real_copy(source, destination)
            if not changed and Path(source).name == "operations.db":
                changed = True
                (self.source / "engagement-ledger.jsonl").write_bytes(b"changed-during-copy\n")
            return result

        with patch.object(snapshot_module.shutil, "copy2", side_effect=copy_and_change):
            manifest = create_snapshot(source_dir=self.source, output_dir=self.output)

        self.assertFalse(manifest["consistent"])
        self.assertFalse(manifest["files"]["engagement-ledger.jsonl"]["consistent"])
        saved = json.loads(
            (self.output / "snapshot-manifest.json").read_text(encoding="utf-8")
        )
        self.assertFalse(saved["consistent"])

    def test_marks_snapshot_inconsistent_when_wal_appears_during_copy(self) -> None:
        real_copy = snapshot_module.shutil.copy2
        created = False

        def copy_and_create_wal(source: Path, destination: Path):
            nonlocal created
            result = real_copy(source, destination)
            if not created and Path(source).name == "operations.db":
                created = True
                (self.source / "operations.db-wal").write_bytes(b"concurrent-write")
            return result

        with patch.object(snapshot_module.shutil, "copy2", side_effect=copy_and_create_wal):
            manifest = create_snapshot(source_dir=self.source, output_dir=self.output)

        self.assertFalse(manifest["consistent"])
        self.assertFalse(manifest["wal_absent_after"])
        self.assertFalse(manifest["wal_absent"])
        saved = json.loads(
            (self.output / "snapshot-manifest.json").read_text(encoding="utf-8")
        )
        self.assertFalse(saved["consistent"])
        self.assertFalse(saved["wal_absent_after"])

    def test_refuses_to_overwrite_existing_snapshot_directory(self) -> None:
        self.output.mkdir()

        with self.assertRaisesRegex(SnapshotError, "snapshot_output_already_exists"):
            create_snapshot(source_dir=self.source, output_dir=self.output)

    def test_snapshot_output_passes_full_paired_engagement_verifier(self) -> None:
        day = "2026-11-16"
        database = self.source / "operations.db"
        database.unlink()
        ledger_events: list[dict[str, object]] = []
        with closing(sqlite3.connect(database)) as connection:
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
            connection.execute(
                "INSERT INTO engagement_runs VALUES(?,?,?,?,?,?,?)",
                (day, "completed", 15, 15, 15, None, ""),
            )
            for index in range(15):
                candidate_id = f"room_{index}"
                room_url = f"https://room.rakuten.co.jp/{candidate_id}/items"
                connection.execute(
                    "INSERT INTO engagement_actions VALUES(?,?,?,?,?,?,?)",
                    (day, candidate_id, room_url, 1, 1, "followed", "liked"),
                )
                ledger_events.append(
                    {
                        "routine_date": day,
                        "candidate_id": candidate_id,
                        "room_url": room_url,
                        "followed": True,
                        "liked": True,
                        "follow_status": "followed",
                        "like_status": "liked",
                    }
                )
            connection.commit()
        (self.source / "engagement-ledger.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in ledger_events),
            encoding="utf-8",
        )
        (self.source / "daily-routine-summary.json").write_text(
            json.dumps(
                {
                    "routine_date": day,
                    "policy_id": EXPECTED_POLICY_ID,
                    "goal": 15,
                    "followed": 15,
                    "liked": 15,
                    "failures": 0,
                    "completed": True,
                    "status": "completed",
                    "error_code": "",
                    "published": True,
                }
            ),
            encoding="utf-8",
        )
        (self.source / "engagement-heartbeat.json").write_text(
            json.dumps(
                {
                    "role": "engagement",
                    "routine_date": day,
                    "status": "completed",
                    "updated_at": f"{day}T05:50:00+09:00",
                }
            ),
            encoding="utf-8",
        )
        (self.source / "engagement-worker.log").write_text(
            f"{day} 05:49:59,000 INFO Engagement progress candidate=room_14 follow=15/15 like=15/15\n"
            f"{day} 05:50:00,000 INFO Public routine progress updated day={day} follow=15 like=15 completed=True\n",
            encoding="utf-8",
        )

        manifest = create_snapshot(source_dir=self.source, output_dir=self.output)
        report = verify_engagement(
            database_path=self.output / "operations.db",
            ledger_path=self.output / "engagement-ledger.jsonl",
            summary_path=self.output / "daily-routine-summary.json",
            heartbeat_path=self.output / "engagement-heartbeat.json",
            log_path=self.output / "engagement-worker.log",
            routine_date=day,
            require_clean_log=True,
        )

        self.assertTrue(manifest["consistent"])
        self.assertTrue(report["ok"])
        self.assertTrue(report["clean_run"])
        self.assertEqual(report["database"]["paired_count"], 15)


if __name__ == "__main__":
    unittest.main()
