from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from room_profile_lock import RoomProfileLockTimeout, room_profile_lock


class RoomProfileLockTest(unittest.TestCase):
    def test_second_holder_times_out_without_entering(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "chrome-profile.lock"
            with room_profile_lock(path):
                with self.assertRaises(RoomProfileLockTimeout):
                    with room_profile_lock(path, timeout_seconds=0):
                        self.fail("second holder must not enter")
            with room_profile_lock(path):
                pass


if __name__ == "__main__":
    unittest.main()
