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

    def test_click_submit_uses_forced_click_after_enabled_check(self) -> None:
        class Submit:
            clicked_force = None

            @staticmethod
            def is_disabled() -> bool:
                return False

            def click(self, *, force: bool) -> None:
                self.clicked_force = force

        submit = Submit()
        RoomPoster._click_submit(submit)
        self.assertTrue(submit.clicked_force)

    def test_click_submit_rejects_disabled_button(self) -> None:
        class Submit:
            @staticmethod
            def is_disabled() -> bool:
                return True

            def click(self, *, force: bool) -> None:
                raise AssertionError("disabled submit should not be clicked")

        with self.assertRaisesRegex(RoomPostError, "無効"):
            RoomPoster._click_submit(Submit())

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

    def test_goto_uses_commit_and_tolerates_slow_domcontentloaded(self) -> None:
        class Page:
            def __init__(self) -> None:
                self.default_timeout = 0
                self.navigation_timeout = 0
                self.goto_args = None
                self.load_timeout = 0

            def set_default_timeout(self, timeout: int) -> None:
                self.default_timeout = timeout

            def set_default_navigation_timeout(self, timeout: int) -> None:
                self.navigation_timeout = timeout

            def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
                self.goto_args = (url, wait_until, timeout)

            def wait_for_load_state(self, state: str, *, timeout: int) -> None:
                self.load_timeout = timeout
                raise TimeoutError

        page = Page()
        RoomPoster(user_data_dir=".", timeout_ms=30_000)._goto(
            page,
            "https://item.rakuten.co.jp/example/item",
        )

        self.assertEqual(
            page.goto_args,
            ("https://item.rakuten.co.jp/example/item", "commit", 60_000),
        )
        self.assertEqual(page.default_timeout, 30_000)
        self.assertEqual(page.navigation_timeout, 60_000)
        self.assertEqual(page.load_timeout, 30_000)


if __name__ == "__main__":
    unittest.main()
