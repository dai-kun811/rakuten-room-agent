from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from room_poster import RoomPostError, build_room_comment


class RoomPosterTest(unittest.TestCase):
    def test_build_room_comment_appends_hashtags(self) -> None:
        self.assertEqual(
            build_room_comment("本文", ["#育児", "#ROOM"]),
            "本文\n\n#育児 #ROOM",
        )

    def test_build_room_comment_rejects_empty_body(self) -> None:
        with self.assertRaises(RoomPostError):
            build_room_comment(" ", ["#ROOM"])


if __name__ == "__main__":
    unittest.main()
