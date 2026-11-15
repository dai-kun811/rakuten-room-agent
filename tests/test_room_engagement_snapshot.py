from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import room_engagement_snapshot as snapshot_module
from room_engagement_snapshot import EVIDENCE_FILES, SnapshotError, create_snapshot


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

        with self.assertRaisesRegex(SnapshotError, "database_wal_present"):
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

    def test_refuses_to_overwrite_existing_snapshot_directory(self) -> None:
        self.output.mkdir()

        with self.assertRaisesRegex(SnapshotError, "snapshot_output_already_exists"):
            create_snapshot(source_dir=self.source, output_dir=self.output)


if __name__ == "__main__":
    unittest.main()
