from __future__ import annotations

import unittest
from pathlib import Path


class RoomRoutinePageTest(unittest.TestCase):
    def test_page_separates_automatic_result_from_manual_browser_state(self) -> None:
        page = (
            Path(__file__).resolve().parents[1] / "room-routine" / "index.html"
        ).read_text(encoding="utf-8")

        self.assertIn('aria-label="自動処理実績"', page)
        self.assertIn('id="autoFollowDone"', page)
        self.assertIn('id="autoLikeDone"', page)
        self.assertIn('id="autoStatus"', page)
        self.assertIn("routine-state", page)
        self.assertIn("raw.githubusercontent.com", page)
        self.assertIn("fetchAutomationProgress", page)
        self.assertIn('aria-label="このブラウザの手動チェック"', page)


if __name__ == "__main__":
    unittest.main()
