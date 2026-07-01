from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from room_poster import (
    ROOM_TRIGGER_SELECTORS,
    SUBMIT_SELECTORS,
    RoomPostError,
    RoomPoster,
    build_room_comment,
)


class RoomPosterTest(unittest.TestCase):
    def test_build_room_comment_appends_hashtags(self) -> None:
        self.assertEqual(
            build_room_comment("本文", ["#育児", "#ROOM"]),
            "本文\n\n#育児 #ROOM",
        )

    def test_build_room_comment_rejects_empty_body(self) -> None:
        with self.assertRaises(RoomPostError):
            build_room_comment(" ", ["#ROOM"])

    def test_current_room_form_selectors_are_supported(self) -> None:
        self.assertIn(
            '[irc="RoomShareButton"] a[href*="room.rakuten.co.jp/mix"]',
            ROOM_TRIGGER_SELECTORS,
        )
        self.assertIn('button:has-text("完了")', SUBMIT_SELECTORS)

    def test_room_api_success_response_confirms_post(self) -> None:
        class Response:
            status = 200

            @staticmethod
            def json() -> dict[str, str]:
                return {"status": "success"}

        self.assertTrue(RoomPoster._response_confirms_post(Response()))

    def test_room_api_error_response_does_not_confirm_post(self) -> None:
        class Response:
            status = 400

            @staticmethod
            def json() -> dict[str, str]:
                return {"status": "error"}

        self.assertFalse(RoomPoster._response_confirms_post(Response()))

    def test_wait_for_item_name_uses_angular_item_signal(self) -> None:
        class Page:
            def __init__(self) -> None:
                self.expression = ""

            def wait_for_function(self, expression: str, *, timeout: int) -> None:
                self.expression = expression
                self.timeout = timeout

        page = Page()
        RoomPoster(user_data_dir=".", timeout_ms=1234)._wait_for_item_name(page)
        self.assertIn("scope?.item?.name", page.expression)
        self.assertEqual(page.timeout, 1234)

    def test_wait_for_item_name_converts_timeout_to_room_error(self) -> None:
        class Page:
            def wait_for_function(self, expression: str, *, timeout: int) -> None:
                raise TimeoutError

        with self.assertRaisesRegex(RoomPostError, "商品名読み込み"):
            RoomPoster(user_data_dir=".")._wait_for_item_name(Page())


if __name__ == "__main__":
    unittest.main()
